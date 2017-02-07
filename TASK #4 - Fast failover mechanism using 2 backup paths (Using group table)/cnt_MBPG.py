from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.topology.event import EventLinkAdd, EventLinkDelete
from ryu.topology import event, switches
from ryu.topology.api import get_switch, get_link
from ryu.lib.packet import ipv4,icmp

from Network import NetworkGraph

class MBPG_Controller(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]



    def __init__(self, *args, **kwargs):
        super(MBPG_Controller, self).__init__(*args, **kwargs)

        self.network = NetworkGraph()
        self.datapaths = {}

        print "Controller Initialized!!"




    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        
        self.datapaths[datapath.id] = datapath
        print "Switch %d added" % (datapath.id)


    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        pkt = packet.Packet(ev.msg.data)
        datapath = ev.msg.datapath
        dpid = datapath.id
        in_port = ev.msg.match['in_port']


        pkt_ipv4 = pkt.get_protocol(ipv4.ipv4)
        if pkt_ipv4 is None: return

        src_ip = pkt_ipv4.src
        dst_ip = pkt_ipv4.dst

        """if src host is not recognized, save it to memory"""
        if self.network.recognizeHost(src_ip)==False:
            self.network.addHost(src_ip)
            self.network.addEdge(src_ip,dpid,0)
            self.network.addEdge(dpid,src_ip,in_port)

        """if dst host is not recognized, return"""
        if self.network.recognizeHost(dst_ip)==False:
            return


        """if both src and dst host are recognized, install paths"""
        print "%s is trying to communicate with %s" %(src_ip,dst_ip)
        self.installMainAndBackupPath(src_ip,dst_ip)
        
    def installMainAndBackupPath(self,src_ip,dst_ip):
        print "Calculating shortest path between %s and %s" % (src_ip,dst_ip)
        mainpath = self.network.getShortestPath(src_ip,dst_ip)
        mainpath = mainpath[1:]
        mainpath = mainpath[0:len(mainpath)-1]

        print "Shortest main path %s" % mainpath

        print "Installing the flow and group entries to switches..."

        for idx in range(0,len(mainpath)):
            dpid1,port1 = mainpath[idx]
            if idx == len(mainpath)-1:
                #install simple flow entry
                datapath = self.datapaths[dpid1]
                parser = datapath.ofproto_parser
                match = parser.OFPMatch(eth_type=0x800, ipv4_dst=dst_ip)
                actions = [parser.OFPActionOutput(port1)]
                self.add_flow(datapath, 500, match, actions)

                self.network.portUsage[dpid1][port1]="M"
                break

            dpid2,port2 = mainpath[idx+1]
            dpid_mid = self.network.findMiddleNode(dpid1,dpid2)
            
            if dpid_mid is None:
                #install simple flow entry
                datapath = self.datapaths[dpid1]
                parser = datapath.ofproto_parser
                match = parser.OFPMatch(eth_type=0x800, ipv4_dst=dst_ip)
                actions = [parser.OFPActionOutput(port1)]
                self.add_flow(datapath, 500, match, actions)

                self.network.portUsage[dpid1][port1]="M"
                continue
            else:
                #install group entry
                dpm_p1 = self.network.findFwdPort(dpid1,dpid_mid)
                dpm_p2 = self.network.findFwdPort(dpid_mid,dpid2)

                #install group entry for (dpid1-port1 to dpid2) , (dpid1-dpm_p1 to dpid_mid) and (dpid1 to controller)
                self.network.portUsage[dpid1][port1]="M"
                
                group_id = self.network.incGroupCount(dpid1)
                print "Adding Group entry %d to switch%d" % (group_id,dpid1)

                self.add_FF_Group_Entry(dpid1,port1,dpm_p1,dst_ip,group_id)

                #install the flow which points to the group table
                datapath = self.datapaths[dpid1]
                parser = datapath.ofproto_parser
                match = parser.OFPMatch(eth_type=0x800, ipv4_dst=dst_ip)
                actions = [parser.OFPActionGroup(group_id)]
                self.add_flow(datapath, 500, match, actions)

                #install flow entry from (dpid_mid-dpm_p2 to dpid2)
                datapath = self.datapaths[dpid_mid]
                parser = datapath.ofproto_parser
                match = parser.OFPMatch(eth_type=0x800, ipv4_dst=dst_ip)
                actions = [parser.OFPActionOutput(dpm_p2)]
                self.add_flow(datapath, 500, match, actions)
        print "Done!"
        return


    
    @set_ev_cls(EventLinkAdd, MAIN_DISPATCHER)
    def _link_add_handler(self, ev):
        self.network.addEdge(  ev.link.src.dpid,  ev.link.dst.dpid,  ev.link.src.port_no  )
        print "Link UP %d-%d -> %d-%d" % (ev.link.src.dpid,  ev.link.src.port_no,  ev.link.dst.dpid,  ev.link.dst.port_no)
        


    @set_ev_cls(EventLinkDelete, MAIN_DISPATCHER)
    def _link_del_handler(self, ev):
        self.network.removeEdge(  ev.link.src.dpid,  ev.link.dst.dpid,  ev.link.src.port_no  )
        print "Link DOWN %d-%d -> %d-%d" % (ev.link.src.dpid,  ev.link.src.port_no,  ev.link.dst.dpid,  ev.link.dst.port_no)

        #install new main path
        endpoints = self.network.getConnectedEndpoints(ev.link.src.dpid,ev.link.src.port_no)
        if endpoints is None: return
        print "Handling the link failure"
        for src,dst in endpoints:
            self.installMainAndBackupPath(src,dst)
            continue

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)

    def remove_table_flows(self, datapath, table_id, match, instructions,out_port):
        """Create OFP flow mod message to remove flows from table."""
        ofproto = datapath.ofproto
        flow_mod = datapath.ofproto_parser.OFPFlowMod(datapath, 0, 0,table_id,ofproto.OFPFC_DELETE,0, 0,1,ofproto.OFPCML_NO_BUFFER,out_port,ofproto.OFPG_ANY, 0,match, instructions)
        return flow_mod

    def add_FF_Group_Entry(self,dpid,port1,port2,dst_ip,group_id):
        datapath = self.datapaths[dpid]
        ofp_parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        match = ofp_parser.OFPMatch(eth_type=0x800, ipv4_dst=dst_ip)

        actions1 = [ofp_parser.OFPActionOutput(port1)]
        weight1 = 0
        watch_port1 = port1
        watch_group1 = ofproto.OFPG_ANY

        actions2 = [ofp_parser.OFPActionOutput(port2)]
        weight2 = 0
        watch_port2 = port2
        watch_group2 = ofproto.OFPG_ANY

        # actions3 = [ofp_parser.OFPActionOutput(ofproto.OFPP_CONTROLLER)]
        # weight3 = 0
        # watch_port3 = ofproto.OFPP_CONTROLLER
        # watch_group3 = ofproto.OFPG_ANY

        buckets = [ofp_parser.OFPBucket(weight1, watch_port1, watch_group1,actions1),ofp_parser.OFPBucket(weight2, watch_port2, watch_group2,actions2)]
        #group_id = self.network.incGroupCount(dpid)
        req = ofp_parser.OFPGroupMod(datapath, ofproto.OFPGC_ADD,ofproto.OFPGT_FF, group_id, buckets)
        datapath.send_msg(req)
        return
