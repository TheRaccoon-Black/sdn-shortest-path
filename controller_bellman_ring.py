# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
# Licensed under the Apache License, Version 2.0
# 
# CONTROLLER RING - FINAL STABILITY FIX & PERFORMANCE OPTIMIZATION
# Perubahan Kritis:
# 1. Mencegah Broadcast Storm (Strict Port Filtering).
# 2. OPTIMASI PERFORMA: Menghapus panggilan API topology_api.get_link() 
#    dari jalur data (flooding) dan menggantinya dengan Dictionary Cache (O(1)).
#    Ini krusial untuk skala 100 Node.

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.topology import event, api as topology_api
from ryu.lib import hub
import networkx as nx

class BellmanRingController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(BellmanRingController, self).__init__(*args, **kwargs)
        self.topology_api_app = self
        self.hosts = {}
        self.net = nx.DiGraph()
        self.mst = None
        # CACHE BARU: Memetakan [dpid][port_no] -> dst_dpid untuk akses cepat
        self.port_map = {} 
        self.last_log_info = (-1, -1, "") 
        
        self.logger.info("BellmanRingController: Versi 100-Node Optimized Siap.")
        self.is_updating = False

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod_class = parser.OFPFlowMod
        if buffer_id:
            mod = mod_class(datapath=datapath, buffer_id=buffer_id,
                            priority=priority, match=match, instructions=inst)
        else:
            mod = mod_class(datapath=datapath, priority=priority,
                            match=match, instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls([event.EventLinkAdd, event.EventLinkDelete, event.EventSwitchEnter])
    def _topology_event_handler(self, ev):
        if not self.is_updating:
            hub.spawn(self._build_optimal_topology)

    def _build_optimal_topology(self):
        self.is_updating = True
        hub.sleep(0.5) 
        
        temp_net = nx.DiGraph()
        # Reset cache map
        temp_port_map = {} 
        
        switches = topology_api.get_switch(self.topology_api_app, None)
        links = topology_api.get_link(self.topology_api_app, None)

        for switch in switches:
            dpid = switch.dp.id
            temp_net.add_node(dpid)
            if dpid not in temp_port_map:
                temp_port_map[dpid] = {}

        for link in links:
            src = link.src.dpid
            dst = link.dst.dpid
            src_port = link.src.port_no
            dst_port = link.dst.port_no
            
            temp_net.add_edge(src, dst, port=src_port)
            temp_net.add_edge(dst, src, port=dst_port)
            
            # Isi cache map
            if src in temp_port_map:
                temp_port_map[src][src_port] = dst
            if dst in temp_port_map:
                temp_port_map[dst][dst_port] = src
        
        self.net = temp_net
        self.port_map = temp_port_map # Update global cache atomic

        if len(self.net.nodes) > 0:
            try:
                undirected = self.net.to_undirected()
                if nx.is_connected(undirected):
                    self.mst = nx.minimum_spanning_tree(undirected)
                else:
                    self.mst = None
            except:
                self.mst = None

        if len(self.net.nodes) > 0:
            link_status = len(self.net.edges)
            ready_msg = "PARTIAL"
            if self.mst: ready_msg = "FULL/MST READY"
            
            current_info = (len(self.net.nodes), link_status, ready_msg)
            
            if current_info != self.last_log_info:
                self.logger.info(">>> Topology Update: %d Switch, %d Link (Status: %s)", 
                                 len(self.net.nodes), link_status, ready_msg)
                self.last_log_info = current_info
        
        self.is_updating = False

    def _intelligent_flood(self, datapath, in_port, msg):
        """
        Versi Optimized: Menggunakan cached self.port_map (O(1) access)
        """
        parser = datapath.ofproto_parser
        actions = []
        all_ports = [p.port_no for p in datapath.ports.values() if p.port_no <= datapath.ofproto.OFPP_MAX]
        dpid = datapath.id
        
        # Ambil map lokal untuk switch ini (jika ada)
        local_map = self.port_map.get(dpid, {})
        
        for port_no in all_ports:
            if port_no == in_port: continue
            
            # ATURAN KHUSUS RING MININET:
            if port_no == 1:
                # Selalu kirim ke host (Port 1)
                actions.append(parser.OFPActionOutput(port_no))
            else:
                # Cek apakah ini port switch menggunakan Cache Map (Sangat Cepat)
                neighbor_dpid = local_map.get(port_no)
                
                if neighbor_dpid:
                    # Ini adalah Link antar switch
                    # HANYA kirim jika MST valid DAN edge ada di MST
                    if self.mst is not None and self.mst.has_edge(dpid, neighbor_dpid):
                        actions.append(parser.OFPActionOutput(port_no))
                else:
                    # Jika tidak ada di map (mungkin link belum terdeteksi),
                    # Asumsikan berbahaya dan JANGAN kirim untuk mencegah loop
                    pass

        if actions:
            out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                      in_port=in_port, actions=actions, data=msg.data)
            datapath.send_msg(out)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP: return

        dst = eth.dst
        src = eth.src
        
        if src not in self.hosts:
            self.hosts[src] = (dpid, in_port)

        # Selalu gunakan Intelligent Flood untuk ARP/Unknown dst
        if eth.ethertype == ether_types.ETH_TYPE_ARP or dst not in self.hosts:
            self._intelligent_flood(datapath, in_port, msg)
            return

        dst_dpid = self.hosts[dst][0]
        
        if dpid == dst_dpid:
            actions = [parser.OFPActionOutput(self.hosts[dst][1])]
        else:
            # Routing Bellman-Ford
            if not self.net.edges or not self.mst:
                self._intelligent_flood(datapath, in_port, msg)
                return
            
            try:
                path = nx.shortest_path(self.net, dpid, dst_dpid, method='bellman-ford')
                next_hop = path[path.index(dpid) + 1]
                out_port = self.net[dpid][next_hop]['port']
                actions = [parser.OFPActionOutput(out_port)]
            except (nx.NetworkXNoPath, nx.NodeNotFound, KeyError):
                self._intelligent_flood(datapath, in_port, msg)
                return

        match = parser.OFPMatch(eth_dst=dst)
        self.add_flow(datapath, 1, match, actions)
        
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=msg.data)
        datapath.send_msg(out)
