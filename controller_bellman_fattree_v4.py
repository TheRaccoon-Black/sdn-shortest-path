# CONTROLLER FAT-TREE - V4: MANAGED FLOODING FOR ARP
# Strategy: Install explicit broadcast flow rules on all switches to flood ARP/broadcast
# This ensures ARP requests/replies propagate through the entire topology

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
import time

class FatTreeBroadcastController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(FatTreeBroadcastController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.logger.info("="*60)
        self.logger.info("Fat-Tree Broadcast Controller V4")
        self.logger.info("="*60)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        
        self.logger.info(f"Switch {dpid:016x} connected")
        
        # Install table-miss: send ALL unknown packets to controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        
        # Install explicit broadcast/flooding rule for broadcast destination
        # This will handle ARP, IPv6 multicast, and IPv4 broadcast
        try:
            # Broadcast MAC (ff:ff:ff:ff:ff:ff)
            match = parser.OFPMatch(eth_dst='ff:ff:ff:ff:ff:ff')
            actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
            self.add_flow(datapath, 1, match, actions)
            self.logger.info(f"Installed broadcast flood rule on switch {dpid:016x}")
        except Exception as e:
            self.logger.warning(f"Could not install broadcast rule: {e}")
        
        self.mac_to_port[dpid] = {}

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        dpid = datapath.id
        
        try:
            pkt = packet.Packet(msg.data)
            eth = pkt.get_protocols(ethernet.ethernet)[0]
            self.logger.info(f"[PKT-IN] Switch {dpid:016x} port {in_port}: {eth.ethertype:#06x} {eth.src}->{eth.dst}")
        except (IndexError, TypeError) as e:
            self.logger.warning(f"[PKT-IN-ERROR] Could not parse packet: {e}")
            return
        
        src = eth.src
        dst = eth.dst
        
        # Ignore LLDP
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        
        # Learn source MAC
        if dpid not in self.mac_to_port:
            self.mac_to_port[dpid] = {}
        self.mac_to_port[dpid][src] = in_port
        
        # For broadcast/multicast (ARP, IPv6 mcast, IPv4 broadcast): ALWAYS FLOOD
        if dst in ['ff:ff:ff:ff:ff:ff'] or dst.startswith('01:00:5e') or dst.startswith('33:33'):
            actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
            self.logger.info(f"[BCAST] Switch {dpid:016x} flooding {eth.ethertype:#06x} from {src} to {dst}")
        # For unicast: use learning table if available, else flood
        elif dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
            actions = [parser.OFPActionOutput(out_port)]
            self.logger.info(f"[UCAST] Switch {dpid:016x} unicasting to port {out_port}")
        else:
            actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
            self.logger.info(f"[UNKNOWN] Switch {dpid:016x} flooding unknown {dst}")
        
        # Send packet out
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=msg.data)
        datapath.send_msg(out)
