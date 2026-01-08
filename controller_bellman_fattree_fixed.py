# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
# Licensed under the Apache License, Version 2.0
# 
# CONTROLLER FAT-TREE - BELLMAN FORD (PURE LLDP MODE)
# 
# CRITICAL FIX: TIDAK MENGGUNAKAN ryu.topology API SAMA SEKALI!
# Topology discovery dilakukan manual via LLDP parsing.
# Ini menghilangkan konflik dengan ryu.topology.switches module.

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, lldp
from ryu.lib import hub
import networkx as nx
from collections import defaultdict
import threading

class BellmanFatTreeController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(BellmanFatTreeController, self).__init__(*args, **kwargs)
        
        # Datapath storage
        self.datapaths = {}  # dpid -> datapath object
        self.datapath_lock = threading.RLock()
        
        # Host tracking
        self.hosts = {}  # mac -> (dpid, port)
        
        # Topology graph
        self.net = nx.DiGraph()
        self.port_map = {}  # dpid -> {port -> neighbor_dpid}
        self.mst = None
        self.topo_lock = threading.RLock()
        
        # Path cache
        self.path_cache = {}
        self.cache_lock = threading.RLock()
        
        # Topology state
        self.topology_ready = False
        self.last_topo_hash = 0
        
        self.logger.info("="*60)
        self.logger.info("Bellman-Ford Fat-Tree Controller (Pure LLDP Mode)")
        self.logger.info("="*60)
        
        # Background workers
        hub.spawn(self._topology_updater)
        hub.spawn(self._path_precompute_worker)
        hub.spawn(self._lldp_sender)

    # =================================================================
    # DATAPATH MANAGEMENT
    # =================================================================

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        """Track datapath connections/disconnections"""
        datapath = ev.datapath
        
        with self.datapath_lock:
            if ev.state == MAIN_DISPATCHER:
                if datapath.id not in self.datapaths:
                    self.logger.info(f'Switch {datapath.id:016x} connected')
                    self.datapaths[datapath.id] = datapath
                    
                    with self.topo_lock:
                        self.net.add_node(datapath.id)
                        if datapath.id not in self.port_map:
                            self.port_map[datapath.id] = {}
                    
            elif ev.state == DEAD_DISPATCHER:
                if datapath.id in self.datapaths:
                    self.logger.info(f'Switch {datapath.id:016x} disconnected')
                    del self.datapaths[datapath.id]
                    
                    with self.topo_lock:
                        if datapath.id in self.net:
                            # Remove all edges connected to this switch
                            edges_to_remove = list(self.net.in_edges(datapath.id)) + \
                                            list(self.net.out_edges(datapath.id))
                            self.net.remove_edges_from(edges_to_remove)
                            self.net.remove_node(datapath.id)
                        
                        if datapath.id in self.port_map:
                            del self.port_map[datapath.id]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Install default flow on new switch"""
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        # Install table-miss flow
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        
        self.logger.info(f'Switch {datapath.id:016x} features installed')

    def add_flow(self, datapath, priority, match, actions, idle_timeout=0, hard_timeout=0):
        """Install flow entry"""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout
        )
        datapath.send_msg(mod)

    # =================================================================
    # LLDP-BASED TOPOLOGY DISCOVERY
    # =================================================================

    def _lldp_sender(self):
        """Periodically send LLDP packets for topology discovery"""
        self.logger.info(">>> LLDP Sender Started")
        
        while True:
            hub.sleep(5.0)
            self._send_lldp_packets()

    def _send_lldp_packets(self):
        """Send LLDP packet out all ports of all switches"""
        with self.datapath_lock:
            datapaths = list(self.datapaths.values())
        
        for datapath in datapaths:
            try:
                ports = datapath.ports.values()
                for port in ports:
                    if port.port_no <= datapath.ofproto.OFPP_MAX:
                        self._send_lldp(datapath, port.port_no)
            except:
                pass

    def _send_lldp(self, datapath, port_no):
        """Send single LLDP packet"""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        # Create LLDP packet
        pkt = packet.Packet()
        pkt.add_protocol(ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_LLDP,
            src='00:00:00:00:00:00',  # Dummy MAC
            dst=lldp.LLDP_MAC_NEAREST_BRIDGE
        ))
        
        # LLDP payload: encode dpid and port
        chassis_id = lldp.ChassisID(
            subtype=lldp.ChassisID.SUB_LOCALLY_ASSIGNED,
            chassis_id=str(datapath.id).encode('ascii')
        )
        port_id = lldp.PortID(
            subtype=lldp.PortID.SUB_LOCALLY_ASSIGNED,
            port_id=str(port_no).encode('ascii')
        )
        ttl = lldp.TTL(ttl=10)
        
        lldp_pkt = lldp.lldp(tlvs=[chassis_id, port_id, ttl, lldp.End()])
        pkt.add_protocol(lldp_pkt)
        pkt.serialize()
        
        # Send out
        actions = [parser.OFPActionOutput(port_no)]
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=pkt.data
        )
        datapath.send_msg(out)

    # =================================================================
    # TOPOLOGY UPDATE
    # =================================================================

    def _topology_updater(self):
        """Periodically rebuild MST and update topology state"""
        self.logger.info(">>> Topology Updater Started")
        
        while True:
            hub.sleep(10.0)
            self._update_topology_state()

    def _update_topology_state(self):
        """Update MST and topology status"""
        with self.topo_lock:
            num_nodes = len(self.net.nodes)
            num_edges = len(self.net.edges)
            
            if num_nodes == 0:
                self.topology_ready = False
                return
            
            # Calculate topology hash
            topo_hash = hash((tuple(sorted(self.net.nodes())), 
                            tuple(sorted(self.net.edges()))))
            
            if topo_hash == self.last_topo_hash:
                return  # No change
            
            self.last_topo_hash = topo_hash
            
            # Rebuild MST
            try:
                undirected = self.net.to_undirected()
                if nx.is_connected(undirected):
                    self.mst = nx.minimum_spanning_tree(undirected)
                    self.topology_ready = True
                    status = "READY"
                else:
                    self.mst = None
                    self.topology_ready = False
                    status = "PARTIAL"
            except:
                self.mst = None
                self.topology_ready = False
                status = "ERROR"
            
            # Clear path cache
            with self.cache_lock:
                self.path_cache.clear()
            
            self.logger.info(
                f">>> Topology Updated: {num_nodes} switches, "
                f"{num_edges} links [{status}]"
            )

    # =================================================================
    # PATH COMPUTATION
    # =================================================================

    def _path_precompute_worker(self):
        """Background path precomputation"""
        self.logger.info(">>> Path Pre-compute Worker Started")
        
        while True:
            hub.sleep(15.0)
            
            if not self.topology_ready or len(self.hosts) < 2:
                continue
            
            self._precompute_paths()

    def _precompute_paths(self):
        """Pre-compute paths using Bellman-Ford"""
        with self.topo_lock:
            if not self.topology_ready:
                return
            net_copy = self.net.copy()
        
        hosts_copy = dict(self.hosts)
        host_switches = set(dpid for dpid, _ in hosts_copy.values())
        
        new_cache = {}
        computed = 0
        
        for src_dpid in host_switches:
            try:
                paths = nx.single_source_bellman_ford_path(net_copy, src_dpid)
                
                for dst_dpid in host_switches:
                    if src_dpid != dst_dpid and dst_dpid in paths:
                        new_cache[(src_dpid, dst_dpid)] = paths[dst_dpid]
                        computed += 1
            except:
                continue
        
        with self.cache_lock:
            self.path_cache.update(new_cache)
        
        if computed > 0:
            self.logger.info(f">>> Pre-computed {computed} paths")

    def _get_path(self, src_dpid, dst_dpid):
        """Get path from cache or compute on-demand"""
        # Try cache
        with self.cache_lock:
            cached = self.path_cache.get((src_dpid, dst_dpid))
            if cached:
                return cached
        
        # Compute on-demand
        with self.topo_lock:
            if not self.topology_ready:
                return None
            
            try:
                path = nx.bellman_ford_path(self.net, src_dpid, dst_dpid)
                with self.cache_lock:
                    self.path_cache[(src_dpid, dst_dpid)] = path
                return path
            except:
                return None

    # =================================================================
    # PACKET HANDLING
    # =================================================================

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

        # Debug: log packet-in events for troubleshooting
        try:
            self.logger.debug(f'PacketIn: dpid={dpid} in_port={in_port} ethertype=0x{eth.ethertype:04x} src={eth.src} dst={eth.dst}')
        except Exception:
            pass
        
        # Handle LLDP for topology discovery
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            self._handle_lldp(datapath, in_port, pkt)
            return
        
        dst = eth.dst
        src = eth.src
        
        # Learn source
        if src not in self.hosts:
            self.hosts[src] = (dpid, in_port)
        
        # Handle ARP or unknown destination
        if eth.ethertype == ether_types.ETH_TYPE_ARP or dst not in self.hosts:
            self._intelligent_flood(datapath, in_port, msg)
            return
        
        # Known destination
        dst_dpid, dst_port = self.hosts[dst]
        
        if dpid == dst_dpid:
            # Same switch
            actions = [parser.OFPActionOutput(dst_port)]
        else:
            # Different switch - get path
            path = self._get_path(dpid, dst_dpid)
            
            if path is None or len(path) < 2:
                self._intelligent_flood(datapath, in_port, msg)
                return
            
            next_hop = path[1]
            
            with self.topo_lock:
                out_port = self.net[dpid][next_hop].get('port')
            
            if out_port is None:
                return
            
            actions = [parser.OFPActionOutput(out_port)]
        
        # Install flow
        match = parser.OFPMatch(eth_dst=dst)
        self.add_flow(datapath, 1, match, actions, idle_timeout=30)
        
        # Send packet
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data
        )
        datapath.send_msg(out)

    def _handle_lldp(self, datapath, in_port, pkt):
        """Parse LLDP packet and update topology"""
        try:
            lldp_pkt = pkt.get_protocol(lldp.lldp)
            if not lldp_pkt:
                return
            
            # Parse LLDP
            chassis_id = None
            port_id = None
            
            for tlv in lldp_pkt.tlvs:
                if isinstance(tlv, lldp.ChassisID):
                    chassis_id = int(tlv.chassis_id.decode('ascii'))
                elif isinstance(tlv, lldp.PortID):
                    port_id = int(tlv.port_id.decode('ascii'))
            
            if chassis_id is None or port_id is None:
                return
            
            # Update topology
            src_dpid = chassis_id
            src_port = port_id
            dst_dpid = datapath.id
            dst_port = in_port
            
            with self.topo_lock:
                # Add link
                self.net.add_edge(dst_dpid, src_dpid, port=dst_port)
                self.net.add_edge(src_dpid, dst_dpid, port=src_port)
                
                # Update port map
                if dst_dpid not in self.port_map:
                    self.port_map[dst_dpid] = {}
                if src_dpid not in self.port_map:
                    self.port_map[src_dpid] = {}
                
                self.port_map[dst_dpid][dst_port] = src_dpid
                self.port_map[src_dpid][src_port] = dst_dpid
                
        except Exception as e:
            pass

    def _intelligent_flood(self, datapath, in_port, msg):
        """Flood packet via MST to avoid loops"""
        parser = datapath.ofproto_parser
        dpid = datapath.id

        # If topology isn't ready yet, fallback to regular flooding so
        # hosts can perform ARP/LLDP and the controller can learn the topology.
        if not self.topology_ready or self.mst is None:
            actions = []
            all_ports = [p.port_no for p in datapath.ports.values() if p.port_no <= datapath.ofproto.OFPP_MAX]
            for port_no in all_ports:
                if port_no == in_port:
                    continue
                actions.append(parser.OFPActionOutput(port_no))

            if actions:
                out = parser.OFPPacketOut(
                    datapath=datapath,
                    buffer_id=msg.buffer_id,
                    in_port=in_port,
                    actions=actions,
                    data=msg.data
                )
                datapath.send_msg(out)
            return
        
        parser = datapath.ofproto_parser
        actions = []
        dpid = datapath.id

        all_ports = [
            p.port_no for p in datapath.ports.values()
            if p.port_no <= datapath.ofproto.OFPP_MAX
        ]

        with self.topo_lock:
            local_map = self.port_map.get(dpid, {})

            for port_no in all_ports:
                if port_no == in_port:
                    continue

                neighbor_dpid = local_map.get(port_no)

                if neighbor_dpid:
                    if self.mst.has_edge(dpid, neighbor_dpid):
                        actions.append(parser.OFPActionOutput(port_no))
                else:
                    # Host port
                    actions.append(parser.OFPActionOutput(port_no))

        if actions:
            out = parser.OFPPacketOut(
                datapath=datapath,
                buffer_id=msg.buffer_id,
                in_port=in_port,
                actions=actions,
                data=msg.data
            )
            datapath.send_msg(out)
