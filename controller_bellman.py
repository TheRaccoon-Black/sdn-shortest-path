# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.topology.api import get_switch, get_link
import networkx as nx

from ryu.lib import stplib

class BellmanFordController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'stplib': stplib.Stp}

    def __init__(self, *args, **kwargs):
        super(BellmanFordController, self).__init__(*args, **kwargs)
        self.topology_api_app = self
        self.hosts = {}

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
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match, instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)

    # ================================================================= #
    # ==================== PERUBAHAN ARSITEKTUR UTAMA =================== #
    # ================================================================= #
    # SEMUA HANDLER TOPOLOGI (EventLinkAdd, EventTopologyChange, dll) DIHAPUS.
    # KITA AKAN MEMBUAT PETA JARINGAN SECARA ON-DEMAND DI DALAM PACKET_IN_HANDLER.
    # ================================================================= #

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        dpid = datapath.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst = eth.dst
        src = eth.src
        
        if src not in self.hosts:
            self.hosts[src] = (dpid, in_port)
            self.logger.info("Belajar host: MAC %s -> S%s Port %s", src, dpid, in_port)

        if eth.ethertype == ether_types.ETH_TYPE_ARP or dst not in self.hosts:
            actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
            out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                      in_port=in_port, actions=actions, data=msg.data)
            datapath.send_msg(out)
            return

        dst_dpid = self.hosts[dst][0]
        
        if dpid == dst_dpid:
            out_port = self.hosts[dst][1]
            actions = [parser.OFPActionOutput(out_port)]
        else:
            # ========================================================= #
            # ======== MEMBUAT PETA JARINGAN SECARA ON-DEMAND ========= #
            # ========================================================= #
            net = nx.DiGraph()
            switches = get_switch(self.topology_api_app, None)
            links = get_link(self.topology_api_app, None)

            for switch in switches:
                net.add_node(switch.dp.id)

            for link in links:
                net.add_edge(link.src.dpid, link.dst.dpid, port=link.src.port_no)
                net.add_edge(link.dst.dpid, link.src.dpid, port=link.dst.port_no)
            # ========================================================= #

            try:
                path = nx.shortest_path(net, dpid, dst_dpid, method='bellman-ford')
                self.logger.info("Jalur dari S%s->S%s: %s", dpid, dst_dpid, path)
                next_hop = path[path.index(dpid) + 1]
                out_port = net[dpid][next_hop]['port']
                actions = [parser.OFPActionOutput(out_port)]
            except (nx.NetworkXNoPath, KeyError):
                self.logger.error("Tidak ada jalur di peta dari S%s ke S%s. Flooding.", dpid, dst_dpid)
                actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]

        match = parser.OFPMatch(eth_dst=dst)
        self.add_flow(datapath, 1, match, actions)
        
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=msg.data)
        datapath.send_msg(out)

