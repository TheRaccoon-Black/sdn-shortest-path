# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
# Licensed under the Apache License, Version 2.0
# 
# CONTROLLER FAT-TREE - BELLMAN FORD (V2 - STABLE)
# 
# Perbaikan:
# 1. Simpler LLDP: Direct topology discovery via LLDP without event handlers
# 2. Decoupled Monitor: Periodic topology update loop
# 3. Robust forwarding: Always flood when unsure, use MST when ready
# 4. Better thread safety: RLock for all shared state

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, lldp, arp
from ryu.lib import hub
import networkx as nx
import threading
from collections import defaultdict

class BellmanFatTreeController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(BellmanFatTreeController, self).__init__(*args, **kwargs)
        
        # Datapath storage
        self.datapaths = {}
        self.dp_lock = threading.RLock()
        
        # Host MAC -> (dpid, port)
        self.hosts = {}
        self.host_lock = threading.RLock()
        
        # Topology: dpid -> {neighbor_dpid -> out_port}
        self.topology = defaultdict(dict)
        self.topo_lock = threading.RLock()
        
        # MST graph
        self.mst = None
        self.topology_ready = False
        
        # Path cache
        self.path_cache = {}
        
        self.logger.info("="*60)
        self.logger.info("Bellman-Ford Fat-Tree Controller V2")
        self.logger.info("="*60)
        
        # Start background workers
        hub.spawn(self._lldp_discovery)
        hub.spawn(self._topology_updater)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        """Track switch connections"""
        datapath = ev.datapath
        dpid = ev.datapath.id
        
        if ev.state == MAIN_DISPATCHER:
            with self.dp_lock:
                if dpid not in self.datapaths:
                    self.datapaths[dpid] = datapath
                    self.logger.info(f"[CONNECT] Switch {dpid:016x} connected (Total: {len(self.datapaths)} switches)")
                    
                    # Add to topology graph
                    with self.topo_lock:
                        if dpid not in self.topology:
                            self.topology[dpid] = {}
                    
        elif ev.state == DEAD_DISPATCHER:
            with self.dp_lock:
                if dpid in self.datapaths:
                    del self.datapaths[dpid]
                    self.logger.info(f"[DISCONNECT] Switch {dpid:016x} disconnected (Total: {len(self.datapaths)} switches)")
                    
                    with self.topo_lock:
                        if dpid in self.topology:
                            # Remove all edges connected to this switch
                            for other in list(self.topology.keys()):
                                if dpid in self.topology[other]:
                                    del self.topology[other][dpid]
                            del self.topology[dpid]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Install default flow on switch"""
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        # Table-miss: send to controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info(f"Switch {datapath.id:016x} configured")

    def add_flow(self, datapath, priority, match, actions):
        """Install flow entry"""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    def _lldp_discovery(self):
        """Send LLDP packets periodically for topology discovery"""
        self.logger.info(">>> LLDP Discovery Started")
        
        while True:
            hub.sleep(3.0)  # Send LLDP every 3 seconds
            
            with self.dp_lock:
                datapaths = list(self.datapaths.values())
            
            for datapath in datapaths:
                try:
                    self._send_lldp_packets(datapath)
                except Exception as e:
                    self.logger.debug(f"LLDP send error: {e}")

    def _send_lldp_packets(self, datapath):
        """Send LLDP on all ports"""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        
        for port in datapath.ports.values():
            if port.port_no <= ofproto.OFPP_MAX:
                # Create LLDP packet with dpid and port in payload
                pkt = packet.Packet()
                pkt.add_protocol(ethernet.ethernet(
                    ethertype=ether_types.ETH_TYPE_LLDP,
                    src='00:00:00:00:00:00',
                    dst=lldp.LLDP_MAC_NEAREST_BRIDGE
                ))
                
                chassis_id = lldp.ChassisID(
                    subtype=lldp.ChassisID.SUB_LOCALLY_ASSIGNED,
                    chassis_id=str(dpid).encode('ascii')
                )
                port_id = lldp.PortID(
                    subtype=lldp.PortID.SUB_LOCALLY_ASSIGNED,
                    port_id=str(port.port_no).encode('ascii')
                )
                ttl = lldp.TTL(ttl=10)
                
                lldp_pkt = lldp.lldp(tlvs=[chassis_id, port_id, ttl, lldp.End()])
                pkt.add_protocol(lldp_pkt)
                pkt.serialize()
                
                # Send out port
                actions = [parser.OFPActionOutput(port.port_no)]
                out = parser.OFPPacketOut(
                    datapath=datapath,
                    buffer_id=ofproto.OFP_NO_BUFFER,
                    in_port=ofproto.OFPP_CONTROLLER,
                    actions=actions,
                    data=pkt.data
                )
                datapath.send_msg(out)

    def _topology_updater(self):
        """Periodically update MST and topology state"""
        self.logger.info(">>> Topology Updater Started")
        
        last_hash = 0
        
        while True:
            hub.sleep(5.0)
            
            with self.topo_lock:
                # Calculate topology hash
                topo_hash = hash((
                    tuple(sorted(self.topology.keys())),
                    tuple(sorted((src, dst) for src in self.topology 
                                for dst in self.topology[src]))
                ))
                
                if topo_hash == last_hash:
                    continue  # No change
                
                last_hash = topo_hash
                
                # Update MST
                try:
                    if len(self.topology) > 0:
                        # Build undirected graph
                        G = nx.Graph()
                        for src in self.topology:
                            G.add_node(src)
                            for dst in self.topology[src]:
                                G.add_edge(src, dst)
                        
                        if nx.is_connected(G):
                            self.mst = G
                            self.topology_ready = True
                            self.path_cache.clear()
                            self.logger.info(
                                f">>> Topology Ready: {len(self.topology)} switches, "
                                f"{sum(len(v) for v in self.topology.values())} edges [MST Ready]"
                            )
                        else:
                            self.topology_ready = False
                            self.logger.info(
                                f">>> Topology Partial: {len(self.topology)} switches "
                                f"[Disconnected]"
                            )
                    else:
                        self.topology_ready = False
                except Exception as e:
                    self.logger.debug(f"MST update error: {e}")
                    self.topology_ready = False

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        """Handle incoming packets"""
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        
        try:
            pkt = packet.Packet(msg.data)
            eth = pkt.get_protocols(ethernet.ethernet)[0]
        except (IndexError, TypeError):
            return
        
        # Ignore LLDP
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            self._handle_lldp(datapath, in_port, pkt)
            return
        
        # Debug: log all non-LLDP packets
        src = eth.src
        dst = eth.dst
        eth_type = f"0x{eth.ethertype:04x}"
        self.logger.info(f"[PKT] dpid={dpid:016x} in_port={in_port} src={src} dst={dst} type={eth_type}")
        
        # Learn source host
        with self.host_lock:
            if src not in self.hosts:
                self.hosts[src] = (dpid, in_port)
        
        # Handle ARP or unknown destination
        if eth.ethertype == ether_types.ETH_TYPE_ARP or dst not in self.hosts:
            self.logger.info(f"[FLOOD] dpid={dpid:016x} type={'ARP' if eth.ethertype == ether_types.ETH_TYPE_ARP else 'unknown_dst'} src={src} dst={dst}")
            self._flood(datapath, in_port, msg)
            return
        
        # Known destination
        with self.host_lock:
            dst_dpid, dst_port = self.hosts[dst]
        
        if dpid == dst_dpid:
            # Same switch: direct forward
            actions = [parser.OFPActionOutput(dst_port)]
        else:
            # Different switch: compute path
            path = self._get_path(dpid, dst_dpid)
            
            if path is None or len(path) < 2:
                self._flood(datapath, in_port, msg)
                return
            
            next_hop = path[1]
            
            with self.topo_lock:
                out_port = self.topology[dpid].get(next_hop)
            
            if out_port is None:
                self._flood(datapath, in_port, msg)
                return
            
            actions = [parser.OFPActionOutput(out_port)]
        
        # Install flow entry
        match = parser.OFPMatch(eth_dst=dst)
        self.add_flow(datapath, 1, match, actions)
        
        # Send packet out
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data
        )
        datapath.send_msg(out)

    def _get_path(self, src_dpid, dst_dpid):
        """Get shortest path from src to dst"""
        # Try cache
        cache_key = (src_dpid, dst_dpid)
        if cache_key in self.path_cache:
            return self.path_cache[cache_key]
        
        with self.topo_lock:
            if not self.topology_ready:
                return None
            
            try:
                # Use MST graph to compute shortest path
                if self.mst and nx.has_path(self.mst, src_dpid, dst_dpid):
                    path = nx.shortest_path(self.mst, src_dpid, dst_dpid)
                    self.path_cache[cache_key] = path
                    return path
            except:
                pass
        
        return None

    def _handle_lldp(self, datapath, in_port, pkt):
        """Parse LLDP and update topology"""
        try:
            lldp_pkt = pkt.get_protocol(lldp.lldp)
            if not lldp_pkt:
                return
            
            # Parse LLDP to extract remote dpid and port
            chassis_id = None
            port_id = None
            
            for tlv in lldp_pkt.tlvs:
                if isinstance(tlv, lldp.ChassisID):
                    chassis_id = int(tlv.chassis_id.decode('ascii'))
                elif isinstance(tlv, lldp.PortID):
                    port_id = int(tlv.port_id.decode('ascii'))
            
            if chassis_id is None or port_id is None:
                return
            
            src_dpid = chassis_id
            src_port = port_id
            dst_dpid = datapath.id
            dst_port = in_port
            
            # Update topology
            with self.topo_lock:
                self.topology[dst_dpid][src_dpid] = dst_port
                self.topology[src_dpid][dst_dpid] = src_port
                
        except Exception as e:
            self.logger.debug(f"LLDP parse error: {e}")

    def _flood(self, datapath, in_port, msg):
        """Flood packet to all ports except incoming"""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        actions = []
        
        self.logger.info(f"[FLOOD_START] dpid={dpid:016x} in_port={in_port} topo_ready={self.topology_ready} mst_ready={self.mst is not None}")
        
        # If MST is ready, only flood on MST ports
        if self.topology_ready and self.mst is not None:
            with self.topo_lock:
                # Flood on switch-to-switch links that are in MST
                for neighbor_dpid, out_port in self.topology[dpid].items():
                    if self.mst.has_edge(dpid, neighbor_dpid):
                        actions.append(parser.OFPActionOutput(out_port))
                        self.logger.debug(f"  -> Add MST port {out_port} to neighbor {neighbor_dpid:016x}")
                
                # Also flood to all host ports (ports not in topology dict values)
                topology_ports = set(self.topology[dpid].values())
            
            # Host ports (not connected to switches)
            for port in datapath.ports.values():
                if port.port_no <= ofproto.OFPP_MAX:
                    if port.port_no not in topology_ports and port.port_no != in_port:
                        actions.append(parser.OFPActionOutput(port.port_no))
                        self.logger.debug(f"  -> Add host port {port.port_no}")
        else:
            # Topology not ready: flood to all ports
            for port in datapath.ports.values():
                if port.port_no <= ofproto.OFPP_MAX:
                    if port.port_no != in_port:
                        actions.append(parser.OFPActionOutput(port.port_no))
                        self.logger.debug(f"  -> Add all port {port.port_no} (topo not ready)")
        
        self.logger.info(f"[FLOOD_SEND] dpid={dpid:016x} actions={len(actions)} ports")
        
        if actions:
            out = parser.OFPPacketOut(
                datapath=datapath,
                buffer_id=msg.buffer_id,
                in_port=in_port,
                actions=actions,
                data=msg.data
            )
            datapath.send_msg(out)
        else:
            self.logger.warn(f"[FLOOD_EMPTY] No actions for flooded packet!")
