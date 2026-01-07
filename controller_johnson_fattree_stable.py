# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
# Licensed under the Apache License, Version 2.0
# 
# CONTROLLER FAT-TREE - JOHNSON (FIXED VERSION)
# Perbaikan untuk Multiple Connection & Convergence Issues

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
import time

class JohnsonFatTreeController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(JohnsonFatTreeController, self).__init__(*args, **kwargs)
        self.topology_api_app = self
        self.hosts = {}
        self.net = nx.DiGraph()
        self.mst = None
        self.all_paths = {} 
        self.port_map = {} 
        self.last_log_info = (-1, -1, "") 
        
        # Stability tracking
        self.stable_counter = 0
        self.last_topology_hash = None
        self.topology_ready = False
        self.computation_in_progress = False
        
        self.logger.info("JohnsonFatTreeController: Started (Stability-Enhanced Mode)")
        hub.spawn(self._monitor_topology)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        # Clear existing flows first
        match = parser.OFPMatch()
        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=match
        )
        datapath.send_msg(mod)
        
        # Install table-miss flow
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None, idle_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                   priority=priority, match=match, 
                                   instructions=inst, idle_timeout=idle_timeout)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                   match=match, instructions=inst,
                                   idle_timeout=idle_timeout)
        datapath.send_msg(mod)

    @set_ev_cls([event.EventLinkAdd, event.EventLinkDelete, event.EventSwitchEnter])
    def _topology_event_ignore(self, ev):
        # Reset stability when topology changes
        self.stable_counter = 0
        self.topology_ready = False

    def _get_topology_hash(self, net):
        """Generate hash for topology state detection"""
        nodes = tuple(sorted(net.nodes()))
        edges = tuple(sorted(net.edges()))
        return hash((nodes, edges))

    def _monitor_topology(self):
        """
        Enhanced monitor with stability checking
        """
        while True:
            hub.sleep(30.0)  # Increased to 30 seconds for Fat-Tree
            
            # Skip if computation is in progress
            if self.computation_in_progress:
                self.logger.info(">>> Computation in progress, skipping update...")
                continue
                
            self._build_optimal_topology()
            gc.collect()

    def _build_optimal_topology(self):
        # 1. Get topology data
        switches = topology_api.get_switch(self.topology_api_app, None)
        links = topology_api.get_link(self.topology_api_app, None)
        
        if not switches:
            self.logger.warning(">>> No switches detected yet...")
            return
        
        temp_net = nx.DiGraph()
        temp_port_map = {} 

        for switch in switches:
            dpid = switch.dp.id
            temp_net.add_node(dpid)
            if dpid not in temp_port_map: 
                temp_port_map[dpid] = {}

        for link in links:
            src, dst = link.src.dpid, link.dst.dpid
            src_port, dst_port = link.src.port_no, link.dst.port_no
            
            temp_net.add_edge(src, dst, port=src_port, weight=1)
            temp_net.add_edge(dst, src, port=dst_port, weight=1)
            
            if src in temp_port_map: 
                temp_port_map[src][src_port] = dst
            if dst in temp_port_map: 
                temp_port_map[dst][dst_port] = src
        
        # 2. Check topology stability
        current_hash = self._get_topology_hash(temp_net)
        
        if current_hash == self.last_topology_hash:
            self.stable_counter += 1
        else:
            self.stable_counter = 0
            self.topology_ready = False
            self.logger.info(">>> Topology changed: %d Switch, %d Link (Resetting stability)", 
                           len(temp_net.nodes), len(temp_net.edges))
        
        self.last_topology_hash = current_hash
        self.net = temp_net
        self.port_map = temp_port_map
        
        # 3. Wait for stability (3 consecutive identical readings = 90 seconds)
        if self.stable_counter < 3:
            self.logger.info(">>> Topology stabilizing... (%d/3) - %d Switch, %d Link", 
                           self.stable_counter, len(self.net.nodes), len(self.net.edges))
            return
        
        # 4. Compute routing only when stable
        if not self.topology_ready:
            self.logger.info(">>> Topology STABLE! Starting route computation...")
            self.computation_in_progress = True
            
            try:
                # Compute MST
                if len(self.net.nodes) > 0:
                    undirected = self.net.to_undirected()
                    if nx.is_connected(undirected):
                        self.logger.info(">>> Computing MST in background...")
                        self.mst = tpool.execute(nx.minimum_spanning_tree, undirected)
                        self.logger.info(">>> MST computed: %d edges", len(self.mst.edges) if self.mst else 0)
                    else:
                        self.logger.warning(">>> Graph not connected, cannot compute MST")
                        self.mst = None
                
                # Compute Johnson (this is heavy)
                if len(self.net.nodes) > 0:
                    self.logger.info(">>> Computing Johnson all-pairs shortest paths...")
                    start_time = time.time()
                    self.all_paths = tpool.execute(nx.johnson, self.net, weight='weight')
                    elapsed = time.time() - start_time
                    
                    num_routes = sum(len(paths) for paths in self.all_paths.values())
                    self.logger.info(">>> Johnson computed: %d routes in %.2f seconds", num_routes, elapsed)
                    
                self.topology_ready = True
                self.logger.info(">>> ROUTING READY! Network can now forward traffic.")
                
            except Exception as e:
                self.logger.error(">>> Route computation failed: %s", str(e))
                self.all_paths = {}
                self.mst = None
            finally:
                self.computation_in_progress = False

    def _intelligent_flood(self, datapath, in_port, msg):
        """Flood only on MST edges to prevent loops"""
        if self.mst is None:
            return  # Don't flood if MST not ready

        parser = datapath.ofproto_parser
        actions = []
        all_ports = [p.port_no for p in datapath.ports.values() 
                    if p.port_no <= datapath.ofproto.OFPP_MAX]
        dpid = datapath.id
        
        for port_no in all_ports:
            if port_no == in_port: 
                continue
            
            local_map = self.port_map.get(dpid, {})
            neighbor_dpid = local_map.get(port_no)
            
            if neighbor_dpid:
                # Only flood on MST edges
                if self.mst.has_edge(dpid, neighbor_dpid):
                    actions.append(parser.OFPActionOutput(port_no))
            else:
                # Always send to host ports
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

        if eth.ethertype == ether_types.ETH_TYPE_LLDP: 
            return

        dst = eth.dst
        src = eth.src
        
        # Learn host location
        if src not in self.hosts:
            self.hosts[src] = (dpid, in_port)

        # Handle ARP or unknown destination
        if eth.ethertype == ether_types.ETH_TYPE_ARP or dst not in self.hosts:
            self._intelligent_flood(datapath, in_port, msg)
            return

        # Route to known destination
        dst_dpid = self.hosts[dst][0]
        
        if dpid == dst_dpid:
            # Same switch - direct output
            actions = [parser.OFPActionOutput(self.hosts[dst][1])]
        else:
            # Different switch - use Johnson routing
            if not self.topology_ready or not self.all_paths:
                # Network not ready, flood via MST
                self._intelligent_flood(datapath, in_port, msg)
                return
            
            if dpid in self.all_paths and dst_dpid in self.all_paths[dpid]:
                try:
                    path = self.all_paths[dpid][dst_dpid]
                    if len(path) < 2:
                        self._intelligent_flood(datapath, in_port, msg)
                        return
                    
                    next_hop = path[1]  # path[0] is current dpid
                    out_port = self.net[dpid][next_hop]['port']
                    actions = [parser.OFPActionOutput(out_port)]
                except Exception as e:
                    self.logger.warning(">>> Routing error: %s", str(e))
                    self._intelligent_flood(datapath, in_port, msg)
                    return
            else:
                self._intelligent_flood(datapath, in_port, msg)
                return

        # Install flow and send packet
        match = parser.OFPMatch(eth_dst=dst)
        self.add_flow(datapath, 1, match, actions, idle_timeout=300)
        
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=msg.data)
        datapath.send_msg(out)