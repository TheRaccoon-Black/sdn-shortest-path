# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
# Licensed under the Apache License, Version 2.0
# 
# CONTROLLER MESH - JOHNSON (FINAL STABLE VERSION)
# 
# Optimasi Maksimal untuk Mesh 20+ Node:
# 1. DECOUPLED MONITOR: Menggunakan Loop 20 detik untuk update topologi.
# 2. THREAD POOL (TPOOL): Menghitung nx.johnson (Sangat Berat) di background.
# 3. PRE-CALCULATED CACHE: Semua rute disimpan di memori, Packet-In hanya lookup O(1).
# 4. STRICT SILENCE: Drop paket total jika Johnson belum siap.

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.topology import event, api as topology_api
from ryu.lib import hub
import networkx as nx
from eventlet import tpool
import gc # Garbage Collector untuk membersihkan RAM setelah hitungan berat

class JohnsonMeshController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(JohnsonMeshController, self).__init__(*args, **kwargs)
        self.topology_api_app = self
        self.hosts = {}
        self.net = nx.DiGraph()
        self.mst = None
        self.all_paths = {} # Cache Rute Johnson
        self.port_map = {} 
        self.last_log_info = (-1, -1, "") 
        self.logger.info("JohnsonMeshController: Siap (FINAL STABLE MODE).")
        
        # Jalankan Monitor Topologi Terpisah
        hub.spawn(self._monitor_topology)

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

    # --- ABAIKAN EVENT LANGSUNG UNTUK MENCEGAH STORM ---
    @set_ev_cls([event.EventLinkAdd, event.EventLinkDelete, event.EventSwitchEnter])
    def _topology_event_ignore(self, ev):
        pass

    def _monitor_topology(self):
        """
        Loop santai 20 detik. Memberi waktu CPU untuk napas
        sebelum menghitung ulang ribuan rute Johnson.
        """
        while True:
            hub.sleep(20.0)
            self._build_optimal_topology()
            gc.collect() # Bersihkan memori sisa perhitungan graph

    def _build_optimal_topology(self):
        # 1. Ambil data topologi terbaru
        switches = topology_api.get_switch(self.topology_api_app, None)
        links = topology_api.get_link(self.topology_api_app, None)
        
        temp_net = nx.DiGraph()
        temp_port_map = {} 

        for switch in switches:
            dpid = switch.dp.id
            temp_net.add_node(dpid)
            if dpid not in temp_port_map: temp_port_map[dpid] = {}

        for link in links:
            src, dst = link.src.dpid, link.dst.dpid
            src_port, dst_port = link.src.port_no, link.dst.port_no
            
            # Weight=1 wajib untuk Johnson
            temp_net.add_edge(src, dst, port=src_port, weight=1)
            temp_net.add_edge(dst, src, port=dst_port, weight=1)
            
            if src in temp_port_map: temp_port_map[src][src_port] = dst
            if dst in temp_port_map: temp_port_map[dst][dst_port] = src
        
        self.net = temp_net
        self.port_map = temp_port_map 

        # 2. Hitung MST (Background Thread)
        if len(self.net.nodes) > 0:
            try:
                undirected = self.net.to_undirected()
                if nx.is_connected(undirected):
                    self.mst = tpool.execute(nx.minimum_spanning_tree, undirected)
                else:
                    self.mst = None
            except:
                self.mst = None

        # 3. Hitung JOHNSON (SANGAT BERAT -> Background Thread)
        if len(self.net.nodes) > 0:
            try:
                # Ini bagian terberat. Kita lempar ke tpool agar controller tidak freeze.
                self.all_paths = tpool.execute(nx.johnson, self.net, weight='weight')
            except Exception:
                self.all_paths = {}
        else:
            self.all_paths = {}

        # 4. Logging
        if len(self.net.nodes) > 0:
            link_status = len(self.net.edges)
            ready_msg = "PARTIAL"
            if self.mst and self.all_paths: ready_msg = "FULL/JOHNSON READY"
            
            current_info = (len(self.net.nodes), link_status, ready_msg)
            if current_info != self.last_log_info:
                self.logger.info(">>> Mesh Update: %d Switch, %d Link (Status: %s)", 
                                 len(self.net.nodes), link_status, ready_msg)
                self.last_log_info = current_info

    def _intelligent_flood(self, datapath, in_port, msg):
        # Strict Silence: Jika MST belum jadi, Drop paket.
        if self.mst is None:
            return

        parser = datapath.ofproto_parser
        actions = []
        all_ports = [p.port_no for p in datapath.ports.values() if p.port_no <= datapath.ofproto.OFPP_MAX]
        dpid = datapath.id
        
        for port_no in all_ports:
            if port_no == in_port: continue
            
            # Cek map lokal (O(1) access)
            local_map = self.port_map.get(dpid, {})
            neighbor_dpid = local_map.get(port_no)
            
            if neighbor_dpid:
                # Link antar switch hanya kirim jika di MST
                if self.mst.has_edge(dpid, neighbor_dpid):
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
            # JOHNSON ROUTING (Lookup Cache)
            # Cek apakah rute tersedia di cache Johnson
            if (self.all_paths and 
                dpid in self.all_paths and 
                dst_dpid in self.all_paths[dpid]):
                
                path = self.all_paths[dpid][dst_dpid]
                next_hop = path[path.index(dpid) + 1]
                out_port = self.net[dpid][next_hop]['port']
                actions = [parser.OFPActionOutput(out_port)]
            else:
                # Jika rute belum ada (tapi MST ada), drop paket unicast
                # untuk mencegah flooding yang tidak perlu.
                return

        match = parser.OFPMatch(eth_dst=dst)
        self.add_flow(datapath, 1, match, actions)
        
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=msg.data)
        datapath.send_msg(out)
