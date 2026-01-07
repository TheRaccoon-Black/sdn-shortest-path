# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
# Licensed under the Apache License, Version 2.0
# 
# CONTROLLER FAT-TREE - JOHNSON V2
# Ultra-Stable Version with Connection Tracking

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
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
        
        # Host tracking
        self.hosts = {}
        
        # Topology
        self.net = nx.DiGraph()
        self.mst = None
        self.all_paths = {} 
        self.port_map = {} 
        
        # Datapath tracking (FIX for multiple connections)
        self.datapaths = {}  # dpid -> datapath object
        self.datapath_list = {}  # Track all seen datapaths
        
        # Stability tracking
        self.stable_counter = 0
        self.last_topology_hash = None
        self.topology_ready = False
        self.computation_in_progress = False
        self.last_computation_time = 0
        
        # Statistics
        self.switch_count = 0
        self.link_count = 0
        
        self.logger.info("="*60)
        self.logger.info("Johnson Fat-Tree Controller V2 - Ultra-Stable")
        self.logger.info("="*60)
        
        hub.spawn(self._monitor_topology)
        hub.spawn(self._stats_monitor)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        """Track datapath connections/disconnections"""
        datapath = ev.datapath
        dpid = datapath.id
        
        if ev.state == MAIN_DISPATCHER:
            if dpid not in self.datapaths:
                self.logger.info(">>> [CONNECT] Switch %016x connected", dpid)
                self.datapaths[dpid] = datapath
                self.datapath_list[dpid] = datapath
                self.switch_count = len(self.datapaths)
            else:
                # This is the "Multiple connections" case
                self.logger.warning(">>> [DUPLICATE] Switch %016x reconnect (ignored)", dpid)
                return
                
        elif ev.state == DEAD_DISPATCHER:
            if dpid in self.datapaths:
                self.logger.warning(">>> [DISCONNECT] Switch %016x disconnected", dpid)
                del self.datapaths[dpid]
                self.switch_count = len(self.datapaths)
                # Reset topology on disconnect
                self.stable_counter = 0
                self.topology_ready = False

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Initial switch setup"""
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        
        self.logger.info(">>> [INIT] Configuring switch %016x", dpid)
        
        # Clear all existing flows
        match = parser.OFPMatch()
        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=match
        )
        datapath.send_msg(mod)
        
        # Install table-miss flow (priority 0)
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None, 
                 idle_timeout=0, hard_timeout=0):
        """Add flow entry to switch"""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        
        if buffer_id:
            mod = parser.OFPFlowMod(
                datapath=datapath, buffer_id=buffer_id,
                priority=priority, match=match, instructions=inst,
                idle_timeout=idle_timeout, hard_timeout=hard_timeout
            )
        else:
            mod = parser.OFPFlowMod(
                datapath=datapath, priority=priority,
                match=match, instructions=inst,
                idle_timeout=idle_timeout, hard_timeout=hard_timeout
            )
        datapath.send_msg(mod)

    @set_ev_cls([event.EventLinkAdd, event.EventLinkDelete, event.EventSwitchEnter, 
                 event.EventSwitchLeave])
    def _topology_change_handler(self, ev):
        """Reset stability on any topology change"""
        self.stable_counter = 0
        self.topology_ready = False

    def _get_topology_hash(self, net):
        """Generate hash for topology state"""
        nodes = tuple(sorted(net.nodes()))
        edges = tuple(sorted(net.edges()))
        return hash((nodes, edges))

    def _stats_monitor(self):
        """Monitor and log statistics every 10 seconds"""
        while True:
            hub.sleep(10.0)
            if self.switch_count > 0:
                status = "READY" if self.topology_ready else f"STABILIZING ({self.stable_counter}/3)"
                self.logger.info(
                    ">>> [STATS] Switches: %d, Links: %d, Hosts: %d, Status: %s",
                    self.switch_count, self.link_count, len(self.hosts), status
                )

    def _monitor_topology(self):
        """Main topology monitoring loop"""
        while True:
            hub.sleep(30.0)
            
            # Skip if less than 2 switches
            if self.switch_count < 2:
                continue
            
            # Skip if computation is in progress
            if self.computation_in_progress:
                self.logger.debug(">>> [SKIP] Computation in progress...")
                continue
            
            # Don't compute too frequently
            if time.time() - self.last_computation_time < 30:
                continue
                
            self._build_topology()
            gc.collect()

    def _build_topology(self):
        """Build network topology and compute routes"""
        # Get topology from Ryu
        switches = topology_api.get_switch(self.topology_api_app, None)
        links = topology_api.get_link(self.topology_api_app, None)
        
        if not switches:
            return
        
        # Build graph
        temp_net = nx.DiGraph()
        temp_port_map = {}
        
        for switch in switches:
            dpid = switch.dp.id
            # Only add switches that are properly connected
            if dpid in self.datapaths:
                temp_net.add_node(dpid)
                if dpid not in temp_port_map:
                    temp_port_map[dpid] = {}
        
        link_count = 0
        for link in links:
            src, dst = link.src.dpid, link.dst.dpid
            src_port, dst_port = link.src.port_no, link.dst.port_no
            
            # Only add links between properly connected switches
            if src in self.datapaths and dst in self.datapaths:
                temp_net.add_edge(src, dst, port=src_port, weight=1)
                temp_net.add_edge(dst, src, port=dst_port, weight=1)
                
                temp_port_map[src][src_port] = dst
                temp_port_map[dst][dst_port] = src
                link_count += 1
        
        self.link_count = link_count
        
        # Check topology stability
        current_hash = self._get_topology_hash(temp_net)
        
        if current_hash == self.last_topology_hash:
            self.stable_counter += 1
        else:
            self.stable_counter = 0
            self.topology_ready = False
            self.logger.info(
                ">>> [TOPO] Changed: %d switches, %d links (resetting stability)",
                len(temp_net.nodes), link_count
            )
        
        self.last_topology_hash = current_hash
        self.net = temp_net
        self.port_map = temp_port_map
        
        # Wait for stability (3 consecutive identical readings)
        if self.stable_counter < 3:
            self.logger.info(
                ">>> [STAB] Waiting for stability (%d/3) - %d switches, %d links",
                self.stable_counter, len(self.net.nodes), link_count
            )
            return
        
        # Compute routes when stable
        if not self.topology_ready:
            self.logger.info(">>> [COMPUTE] Topology stable! Computing routes...")
            self.computation_in_progress = True
            self.last_computation_time = time.time()
            
            try:
                # Compute MST
                if len(self.net.nodes) >= 2:
                    undirected = self.net.to_undirected()
                    if nx.is_connected(undirected):
                        self.logger.info(">>> [MST] Computing minimum spanning tree...")
                        start = time.time()
                        self.mst = tpool.execute(nx.minimum_spanning_tree, undirected)
                        elapsed = time.time() - start
                        self.logger.info(
                            ">>> [MST] Done: %d edges in %.2fs",
                            len(self.mst.edges) if self.mst else 0, elapsed
                        )
                    else:
                        self.logger.warning(">>> [MST] Graph not connected!")
                        self.mst = None
                
                # Compute Johnson all-pairs shortest paths
                if len(self.net.nodes) >= 2:
                    self.logger.info(">>> [JOHNSON] Computing all-pairs shortest paths...")
                    start = time.time()
                    self.all_paths = tpool.execute(nx.johnson, self.net, weight='weight')
                    elapsed = time.time() - start
                    
                    num_routes = sum(len(paths) for paths in self.all_paths.values())
                    self.logger.info(
                        ">>> [JOHNSON] Done: %d routes in %.2fs",
                        num_routes, elapsed
                    )
                
                self.topology_ready = True
                self.logger.info(">>> [READY] ✓ Network ready for traffic!")
                
            except Exception as e:
                self.logger.error(">>> [ERROR] Route computation failed: %s", str(e))
                self.all_paths = {}
                self.mst = None
                self.topology_ready = False
            finally:
                self.computation_in_progress = False

    def _intelligent_flood(self, datapath, in_port, msg):
        """Flood packet only on MST edges"""
        if self.mst is None:
            return
        
        parser = datapath.ofproto_parser
        actions = []
        dpid = datapath.id
        
        # Get all valid ports
        try:
            all_ports = [p.port_no for p in datapath.ports.values() 
                        if p.port_no <= datapath.ofproto.OFPP_MAX]
        except:
            return
        
        for port_no in all_ports:
            if port_no == in_port:
                continue
            
            local_map = self.port_map.get(dpid, {})
            neighbor_dpid = local_map.get(port_no)
            
            if neighbor_dpid:
                # Only flood on MST edges to prevent loops
                if self.mst.has_edge(dpid, neighbor_dpid):
                    actions.append(parser.OFPActionOutput(port_no))
            else:
                # Always send to host ports
                actions.append(parser.OFPActionOutput(port_no))
        
        if actions:
            out = parser.OFPPacketOut(
                datapath=datapath, buffer_id=msg.buffer_id,
                in_port=in_port, actions=actions, data=msg.data
            )
            datapath.send_msg(out)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        """Handle incoming packets"""
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        
        # Ignore LLDP
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        
        dst = eth.dst
        src = eth.src
        
        # Learn source host location
        if src not in self.hosts:
            self.hosts[src] = (dpid, in_port)
        
        # Handle ARP or unknown destination
        if eth.ethertype == ether_types.ETH_TYPE_ARP or dst not in self.hosts:
            self._intelligent_flood(datapath, in_port, msg)
            return
        
        # Route to known destination
        dst_dpid, dst_port = self.hosts[dst]
        
        if dpid == dst_dpid:
            # Same switch - direct output
            actions = [parser.OFPActionOutput(dst_port)]
        else:
            # Different switch - use Johnson routing
            if not self.topology_ready or not self.all_paths:
                self._intelligent_flood(datapath, in_port, msg)
                return
            
            if dpid not in self.all_paths or dst_dpid not in self.all_paths[dpid]:
                self._intelligent_flood(datapath, in_port, msg)
                return
            
            try:
                path = self.all_paths[dpid][dst_dpid]
                if len(path) < 2:
                    self._intelligent_flood(datapath, in_port, msg)
                    return
                
                next_hop = path[1]  # path[0] is current dpid
                out_port = self.net[dpid][next_hop]['port']
                actions = [parser.OFPActionOutput(out_port)]
            except Exception as e:
                self.logger.debug(">>> [ROUTE] Error: %s", str(e))
                self._intelligent_flood(datapath, in_port, msg)
                return
        
        # Install flow entry
        match = parser.OFPMatch(eth_dst=dst)
        self.add_flow(datapath, 1, match, actions, idle_timeout=300)
        
        # Send packet out
        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions, data=msg.data
        )
        datapath.send_msg(out)