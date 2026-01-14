# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
# Licensed under the Apache License, Version 2.0
#
# CONTROLLER MESH - JOHNSON (LOCK MODE / HIGH SPEC)
#
# Strategi Akhir:
# 1. TARGET LOCK: Jika link mencapai 2450 (100% untuk 50 Node),
#    Controller akan BERHENTI MEMANTAU topologi selamanya.
# 2. CPU SAVING: 100% Resource CPU dialihkan untuk forwarding paket.
# 3. ARP PROXY: Aktif untuk mencegah broadcast storm.

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
import gc

class JohnsonMeshUltraController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(JohnsonMeshUltraController, self).__init__(*args, **kwargs)
        self.topology_api_app = self
        self.hosts = {}
        self.arp_table = {}
        self.net = nx.DiGraph()
        self.mst = None
        self.all_paths = {}
        self.port_map = {}
       
        self.last_topo_stats = (-1, -1)
        self.is_ready = False
        self.topology_frozen = False # Fitur Kunci Topologi
       
        self.logger.info("JohnsonMeshUltraController: Siap (LOCK MODE - Target 2450 Link).")
        hub.spawn(self._monitor_topology)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
       
        # 1. Default: DROP
        match = parser.OFPMatch()
        actions = []
        self.add_flow(datapath, 0, match, actions)

        # 2. LLDP: TO CONTROLLER
        match_lldp = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_LLDP)
        actions_ctrl = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                               ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 65535, match_lldp, actions_ctrl)

        # 3. ARP: TO CONTROLLER
        match_arp = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_ARP)
        self.add_flow(datapath, 100, match_arp, actions_ctrl)

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
    def _topology_event_ignore(self, ev):
        pass

    def _monitor_topology(self):
        # Warmup 5 Menit
        self.logger.info(">>> WARMUP: Menunggu 300 detik agar switch connect...")
        hub.sleep(300.0)
        self.logger.info(">>> WARMUP SELESAI. Memulai scanning...")

        while True:
            # Jika sudah dikunci, berhenti memantau!
            if self.topology_frozen:
                self.logger.info(">>> TOPOLOGY LOCKED. Monitoring Stopped (CPU Saving).")
                break # Keluar dari loop monitoring selamanya

            switches = topology_api.get_switch(self.topology_api_app, None)
            links = topology_api.get_link(self.topology_api_app, None)
           
            sw_count = len(switches)
            lnk_count = len(links)
            current_stats = (sw_count, lnk_count)
           
            # Target Mesh 50 = 2450 Link
            TARGET_LINKS = 2450
           
            if current_stats != self.last_topo_stats:
                self.logger.info(f">>> Status: {sw_count} Sw, {lnk_count} Link. (Target Lock: {TARGET_LINKS})")
                self.last_topo_stats = current_stats

            # JIKA MENCAPAI TARGET 100% -> HITUNG SEKALI & KUNCI
            if lnk_count >= TARGET_LINKS:
                self.logger.info(f">>> TARGET TERCAPAI ({lnk_count} Link). MENGHITUNG & MENGUNCI...")
               
                safe_links = [(l.src.dpid, l.dst.dpid, l.src.port_no, l.dst.port_no) for l in links]
                safe_switches = [s.dp.id for s in switches]
               
                # Hitung langsung (Blocking tidak masalah karena ini langkah terakhir)
                self._calculate_logic(safe_switches, safe_links)
               
                # SET LOCK
                self.topology_frozen = True
                self.is_ready = True
                gc.collect()
           
            hub.sleep(10.0)

    def _calculate_logic(self, switch_ids, link_list):
        try:
            temp_net = nx.DiGraph()
            temp_port_map = {}

            for dpid in switch_ids:
                temp_net.add_node(dpid)
                if dpid not in temp_port_map: temp_port_map[dpid] = {}

            for src, dst, sport, dport in link_list:
                temp_net.add_edge(src, dst, port=sport, weight=1)
                temp_net.add_edge(dst, src, port=dport, weight=1)
                if src in temp_port_map: temp_port_map[src][sport] = dst
                if dst in temp_port_map: temp_port_map[dst][dport] = src
           
            # Hitung MST & Johnson
            undirected = temp_net.to_undirected()
            if nx.is_connected(undirected):
                mst = nx.minimum_spanning_tree(undirected)
            else:
                mst = None

            all_paths = nx.johnson(temp_net, weight='weight')

            self.net = temp_net
            self.port_map = temp_port_map
            self.mst = mst
            self.all_paths = all_paths
           
            if self.mst and self.all_paths:
                self.logger.info(f">>> STATUS: SYSTEM LOCKED & READY. Traffic Allowed.")
           
        except Exception as e:
            self.logger.error(f"Calculation Error: {e}")

    # --- ARP PROXY ---
    def _handle_arp(self, datapath, in_port, pkt_arp, eth):
        src_ip = pkt_arp.src_ip
        src_mac = pkt_arp.src_mac
        dst_ip = pkt_arp.dst_ip

        self.arp_table[src_ip] = src_mac
        self.hosts[src_mac] = (datapath.id, in_port)

        if pkt_arp.opcode == arp.ARP_REQUEST:
            if dst_ip in self.arp_table:
                dst_mac = self.arp_table[dst_ip]
                self._send_arp_reply(datapath, in_port, src_mac, src_ip, dst_mac, dst_ip)
                return True
        return False

    def _send_arp_reply(self, datapath, port, src_mac, src_ip, target_mac, target_ip):
        pkt = packet.Packet()
        pkt.add_protocol(ethernet.ethernet(ethertype=ether_types.ETH_TYPE_ARP,
                                           dst=src_mac, src=target_mac))
        pkt.add_protocol(arp.arp(opcode=arp.ARP_REPLY,
                                 src_mac=target_mac, src_ip=target_ip,
                                 dst_mac=src_mac, dst_ip=src_ip))
        pkt.serialize()
        actions = [datapath.ofproto_parser.OFPActionOutput(port)]
        out = datapath.ofproto_parser.OFPPacketOut(
            datapath=datapath, buffer_id=datapath.ofproto.OFP_NO_BUFFER,
            in_port=datapath.ofproto.OFPP_CONTROLLER, actions=actions, data=pkt.data)
        datapath.send_msg(out)

    def _intelligent_flood(self, datapath, in_port, msg):
        if self.mst is None: return

        parser = datapath.ofproto_parser
        actions = []
        all_ports = [p.port_no for p in datapath.ports.values() if p.port_no <= datapath.ofproto.OFPP_MAX]
        dpid = datapath.id
       
        for port_no in all_ports:
            if port_no == in_port: continue
            local_map = self.port_map.get(dpid, {})
            neighbor = local_map.get(port_no)
           
            if neighbor:
                if self.mst.has_edge(dpid, neighbor):
                    actions.append(parser.OFPActionOutput(port_no))
            else:
                actions.append(parser.OFPActionOutput(port_no))

        if actions:
            out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                      in_port=in_port, actions=actions, data=msg.data)
            datapath.send_msg(out)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        if not self.is_ready: return

        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP: return

        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            pkt_arp = pkt.get_protocols(arp.arp)[0]
            if self._handle_arp(datapath, in_port, pkt_arp, eth):
                return

        dst = eth.dst
        src = eth.src
       
        if src not in self.hosts:
            self.hosts[src] = (dpid, in_port)

        if dst == 'ff:ff:ff:ff:ff:ff' or dst not in self.hosts:
            self._intelligent_flood(datapath, in_port, msg)
            return

        dst_dpid = self.hosts[dst][0]
       
        if dpid == dst_dpid:
            actions = [parser.OFPActionOutput(self.hosts[dst][1])]
        else:
            if (self.all_paths and
                dpid in self.all_paths and
                dst_dpid in self.all_paths[dpid]):
                try:
                    path = self.all_paths[dpid][dst_dpid]
                    next_hop = path[path.index(dpid) + 1]
                    out_port = self.net[dpid][next_hop]['port']
                    actions = [parser.OFPActionOutput(out_port)]
                except:
                    self._intelligent_flood(datapath, in_port, msg)
                    return
            else:
                self._intelligent_flood(datapath, in_port, msg)
                return

        match = parser.OFPMatch(eth_dst=dst)
        self.add_flow(datapath, 1, match, actions)
       
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=msg.data)
        datapath.send_msg(out) 