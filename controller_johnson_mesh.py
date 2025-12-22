# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
# Licensed under the Apache License, Version 2.0
# 
# CONTROLLER MESH - JOHNSON (THREAD POOL FIX)
# Optimasi Kritis:
# 1. Menggunakan 'eventlet.tpool' untuk memindahkan kalkulasi berat ke thread terpisah.
#    Ini MENCEGAH Controller 'membeku' (freeze) saat menghitung ribuan rute.
# 2. Batching Time dinaikkan ke 30 detik (Aggressive Batching untuk Mesh 50+).

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.topology import event, api as topology_api
from ryu.lib import hub
import networkx as nx
from eventlet import tpool # Library wajib untuk threading di Ryu

class JohnsonMeshController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(JohnsonMeshController, self).__init__(*args, **kwargs)
        self.topology_api_app = self
        self.hosts = {}
        self.net = nx.DiGraph()
        self.mst = None
        self.all_paths = {} 
        self.port_map = {} 
        self.last_log_info = (-1, -1, "") 
        self.logger.info("JohnsonMeshController: Siap (Thread Pool Mode 30s Batch).")
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
        
        # --- BATCHING 30 DETIK ---
        # Kita naikkan ke 30 detik. Biarkan Link Discovery bekerja tenang dulu.
        hub.sleep(30.0) 
        
        # Bangun graph lokal dulu (Cepat)
        temp_net = nx.DiGraph()
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
            
            # Weight=1 untuk Johnson
            temp_net.add_edge(src, dst, port=src_port, weight=1)
            temp_net.add_edge(dst, src, port=dst_port, weight=1)
            
            if src in temp_port_map:
                temp_port_map[src][src_port] = dst
            if dst in temp_port_map:
                temp_port_map[dst][dst_port] = src
        
        self.net = temp_net
        self.port_map = temp_port_map 

        # --- HEAVY CALCULATION (THREAD POOL) ---
        # Kita pindahkan hitungan berat ke thread lain agar Main Loop tidak macet
        
        # 1. HITUNG MST
        if len(self.net.nodes) > 0:
            try:
                undirected = self.net.to_undirected()
                if nx.is_connected(undirected):
                    # tpool.execute mencegah blocking
                    self.mst = tpool.execute(nx.minimum_spanning_tree, undirected)
                else:
                    self.mst = None
            except:
                self.mst = None

        # 2. HITUNG JOHNSON (SANGAT BERAT UNTUK MESH)
        if len(self.net.nodes) > 0:
            try:
                # Ini yang biasanya bikin macet. Sekarang aman di thread pool.
                self.all_paths = tpool.execute(nx.johnson, self.net, weight='weight')
            except Exception:
                self.all_paths = {}
        else:
            self.all_paths = {}

        # LOGGING
        if len(self.net.nodes) > 0:
            link_status = len(self.net.edges)
            ready_msg = "PARTIAL"
            if self.mst and self.all_paths: ready_msg = "FULL/JOHNSON READY"
            
            current_info = (len(self.net.nodes), link_status, ready_msg)
            if current_info != self.last_log_info:
                self.logger.info(">>> Mesh Update: %d Switch, %d Link (Status: %s)", 
                                 len(self.net.nodes), link_status, ready_msg)
                self.last_log_info = current_info
        
        self.is_updating = False

    def _intelligent_flood(self, datapath, in_port, msg):
        parser = datapath.ofproto_parser
        actions = []
        all_ports = [p.port_no for p in datapath.ports.values() if p.port_no <= datapath.ofproto.OFPP_MAX]
        dpid = datapath.id
        local_map = self.port_map.get(dpid, {})
        
        for port_no in all_ports:
            if port_no == in_port: continue
            
            neighbor_dpid = local_map.get(port_no)
            if neighbor_dpid:
                # Link antar switch hanya kirim jika di MST
                if self.mst is not None and self.mst.has_edge(dpid, neighbor_dpid):
                    actions.append(parser.OFPActionOutput(port_no))
            else:
                # Port Host
                actions.append(parser.OFPActionOutput(port_no))

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

        if eth.ethertype == ether_types.ETH_TYPE_ARP or dst not in self.hosts:
            self._intelligent_flood(datapath, in_port, msg)
            return

        dst_dpid = self.hosts[dst][0]
        
        if dpid == dst_dpid:
            actions = [parser.OFPActionOutput(self.hosts[dst][1])]
        else:
            # JOHNSON ROUTING
            if (self.all_paths and 
                dpid in self.all_paths and 
                dst_dpid in self.all_paths[dpid]):
                
                path = self.all_paths[dpid][dst_dpid]
                next_hop = path[path.index(dpid) + 1]
                out_port = self.net[dpid][next_hop]['port']
                actions = [parser.OFPActionOutput(out_port)]
            else:
                self._intelligent_flood(datapath, in_port, msg)
                return

        match = parser.OFPMatch(eth_dst=dst)
        self.add_flow(datapath, 1, match, actions)
        
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=msg.data)
        datapath.send_msg(out)
