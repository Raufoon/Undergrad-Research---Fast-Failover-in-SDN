from os import system
from time import time

print "disabling port s3-eth2"
system('sudo ovs-ofctl mod-port s5 s5-eth1 down')
print "Port s3-eth2 was disabled at %.10f" % time()
