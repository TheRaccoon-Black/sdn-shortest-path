# CONTROLLER FAT-TREE - BELLMAN FORD V3 (SIMPLEST POSSIBLE)
# Strategy: Stop overthinking. Just do basic L2 learning + forwarding.
# - No LLDP detection (let Ryu's built-in LLDP handle it or use table-miss)
# - Simple MAC learning
# - Flood unknown destinations
# - Install unicast flows when learned

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types

class SimpleFatTreeController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SimpleFatTreeController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.logger.info("="*60)
        self.logger.info("Simple Fat-Tree Controller V3 (L2 Learning)")
        self.logger.info("="*60)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        # Install table-miss: send to controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info(f"Switch {datapath.id:016x} configured")

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
        
        try:
            pkt = packet.Packet(msg.data)
            eth = pkt.get_protocols(ethernet.ethernet)[0]
            self.logger.info(f"[PACKET] Switch {datapath.id:016x} port {in_port}: {eth.src}->{eth.dst} type={eth.ethertype:#06x}")
        except (IndexError, TypeError) as e:
            self.logger.error(f"[ERROR] Failed to parse packet: {e}")
            return
        
        # Ignore LLDP
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            self.logger.debug(f"[LLDP] Ignoring LLDP from {eth.src}")
            return
        
        dpid = datapath.id
        src = eth.src
        dst = eth.dst
        
        # Learn MAC to port
        if dpid not in self.mac_to_port:
            self.mac_to_port[dpid] = {}
        
        self.mac_to_port[dpid][src] = in_port
        
        # For ARP, always flood
        if eth.ethertype == 0x0806:  # ARP
            out_port = ofproto.OFPP_FLOOD
            self.logger.info(f"[ARP] Flooding ARP from {eth.src} to {eth.dst}")
        # If destination is known, unicast; otherwise flood
        elif dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
            self.logger.debug(f"[LEARN] Unicasting to {dst} via port {out_port}")
        else:
            out_port = ofproto.OFPP_FLOOD
            self.logger.debug(f"[FLOOD] Unknown {dst}, flooding")
        
        actions = [parser.OFPActionOutput(out_port)]
        
        # Install flow if not flooding
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(eth_dst=dst)
            self.add_flow(datapath, 1, match, actions)
        
        # Send packet out
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=msg.data)
        datapath.send_msg(out)
        self.logger.debug(f"[SEND] Sent packet out port {out_port}")

