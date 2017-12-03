# MIT License

# Copyright (c) 2017 Balazs Bucsay

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import sys

if "interface.py" in sys.argv[0]:
	print "[-] Instead of poking around just try: python xfltreat.py --help"
	sys.exit(-1)


import socket
import struct
import fcntl
import time
import os
import subprocess

import common

class Interface():
	IFF_TUN = 0x0001
	IFF_TAP = 0x0002
	IFF_NO_PI = 0x1000

	CLONEDEV_LINUX = "/dev/net/tun"
	IOCTL_LINUX_TUNSETIFF = 0x400454ca
	IOCTL_LINUX_SIOCSIFADDR = 0x8916
	IOCTL_LINUX_SIOCSIFNETMASK = 0x891C
	IOCTL_LINUX_SIOCSIFMTU = 0x8922

	IOCTL_MACOSX_SIOCSIFADDR = 0x8020690c
	IOCTL_MACOSX_SIOCSIFNETMASK = 0x80206916
	IOCTL_MACOSX_SIOCSIFMTU = 0x80206934
	IOCTL_MACOSX_SIOCSIFFLAGS = 0x80206910
	IOCTL_MACOSX_SIOCAIFADDR = 0x8040691A

	MACOS_UTUN_CONTROL_NAME = "com.apple.net.utun_control"
	MACOS_PF_SYSTEM = 32
	MACOS_AF_SYSTEM = 32
	MACOS_SYSPROTO_CONTROL = 2
	MACOS_AF_SYS_CONTROL = 2
	MACOS_UTUN_OPT_IFNAME = 2
	MACOS_MAX_KCTL_NAME = 96
	MACOS_CTLIOCGINFO = 0xc0644e03
	temp = None

	WINDOWS_ADAPTER_KEY = "SYSTEM\\CurrentControlSet\\Control\\Class\\{4D36E972-E325-11CE-BFC1-08002BE10318}"

	orig_default_gw = None

	def __init__(self):
		self.os_type = common.get_os_type()
		if self.os_type == common.OS_LINUX:
			import pyroute2
			self.ip = pyroute2.IPRoute()

	# allocating tunnel, clonde device and name it
	def tun_alloc(self, dev, flags):
		if self.os_type == common.OS_LINUX:
			try:
				tun = os.open(Interface.CLONEDEV_LINUX, os.O_RDWR|os.O_NONBLOCK, 0)
				ifr = struct.pack('16sH', dev, flags)
				fcntl.ioctl(tun, self.IOCTL_LINUX_TUNSETIFF, ifr)

			except IOError:
				common.internal_print("Error: Cannot create tunnel. Is {0} in use?".format(dev), -1)
				sys.exit(-1)
		
		if self.os_type == common.OS_MACOSX:
			'''
			# before utun, tun/tap driver had to be used. utun support was 
			# added to MacOS 10.7+ so there is no need for tun/tap ext.
			if common.get_os_release() == '13.4.0':
				#TODO loop to look for an interface that is not busy
				for i in range(0, 16):
					self.iface_name = "tun{0}".format(i)
					try:
						tun = os.open("/dev/"+self.iface_name, os.O_EXCL|os.O_RDWR, 0)
						print tun
					except Exception as exception:
						if exception.args[0] == 16:
							continue
						else:
							print exception
							sys.exit(-1)
					break
			else:
			'''
			# MacOS utun support
			# direct calls to libc are needed, because otherwise it could not
			# done.
			import ctypes
			import ctypes.util

			self.iface_name = "\x00"*10
			libc_name = ctypes.util.find_library('c')
			libc = ctypes.CDLL(libc_name, use_errno=True)

			# special socket to poke MacOS(X)' soul
			s = socket.socket(self.MACOS_PF_SYSTEM, socket.SOCK_DGRAM, self.MACOS_SYSPROTO_CONTROL)

			# magic to make utun alive
			info = struct.pack("<I{0}s".format(self.MACOS_MAX_KCTL_NAME), 0, self.MACOS_UTUN_CONTROL_NAME)
			ctl_id = struct.unpack("<I{0}s".format(self.MACOS_MAX_KCTL_NAME), fcntl.ioctl(s, self.MACOS_CTLIOCGINFO, info))[0]

			# setting up the address, because the python lib does not
			# support this type of address type...
			# setting the interface number to 0 to let the kernel allocate
			addr = struct.pack("<BBHIIIIIIII", 32, self.MACOS_AF_SYSTEM, self.MACOS_AF_SYS_CONTROL, ctl_id, 0, 0, 0, 0, 0, 0, 0)
			err = libc.connect(s.fileno(), addr, 32)
			if err < 0:
				err = ctypes.get_errno()
				raise OSError(err, os.strerror(err))

			# get interface name into the self.iface_name
			err = libc.getsockopt(s.fileno(), self.MACOS_SYSPROTO_CONTROL, self.MACOS_UTUN_OPT_IFNAME, ctypes.c_char_p(self.iface_name), ctypes.byref(ctypes.c_int(10)))
			if err < 0:
				err = ctypes.get_errno()
				raise OSError(err, os.strerror(err))

			# setting flags on interface/fd
			fcntl.fcntl(s, fcntl.F_SETFL, os.O_NONBLOCK)
			fcntl.fcntl(s, fcntl.F_SETFD, fcntl.FD_CLOEXEC)

			# saving the socket, otherwise it will be destroyed. with the iface
			self.temp = s
			return s.fileno()


		if self.os_type == common.OS_WINDOWS:
			#def CTL_CODE(device_type, function, melthod, access):
			#	return (device_type << 16) | (access << 14) | (function << 2) | method
			##define TAP_WIN_CONTROL_CODE(request,method) \
			#  CTL_CODE (FILE_DEVICE_UNKNOWN, request, method, FILE_ANY_ACCESS)
			##define TAP_WIN_IOCTL_CONFIG_TUN            TAP_WIN_CONTROL_CODE (10, METHOD_BUFFERED)
			return None



		return tun

	# setting MTU on the interface
	def set_mtu(self, dev, mtu):
		s = socket.socket(type=socket.SOCK_DGRAM)
		try:
			if self.os_type == common.OS_LINUX:
				ifr = struct.pack('<16sH', dev, mtu) + '\x00'*14
				fcntl.ioctl(s, self.IOCTL_LINUX_SIOCSIFMTU, ifr)
			if self.os_type == common.OS_MACOSX:
				ifr = struct.pack('<16sH', self.iface_name, 1350)+'\x00'*14
				fcntl.ioctl(s, self.IOCTL_MACOSX_SIOCSIFMTU, ifr)
		except Exception as e:
			common.internal_print("Cannot set MTU ({0}) on interface".format(mtu), -1)
			sys.exit(-1)

		return

	# setting IP address + netmask on the interface
	def set_ip_address(self, dev, ip, serverip, netmask):
		if self.os_type == common.OS_LINUX:
			idx = self.ip.link_lookup(ifname=dev)[0]
			self.ip.addr('add', index=idx, address=ip, mask=int(netmask))
			self.ip.link('set', index=idx, state='up')

		if self.os_type == common.OS_MACOSX:
			ifr = struct.pack('<16sBBHIIIBBHIIIBBHIII', 
				self.iface_name,
				16, socket.AF_INET, 0, struct.unpack('<L', socket.inet_pton(socket.AF_INET, ip))[0], 0, 0,
				16, socket.AF_INET, 0, struct.unpack('<L', socket.inet_pton(socket.AF_INET, serverip))[0], 0, 0,
				16, 0, 0, struct.unpack('<L', socket.inet_pton(socket.AF_INET, "255.255.255.255"))[0], 0, 0)
			try:
				sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
				fcntl.ioctl(sock, self.IOCTL_MACOSX_SIOCAIFADDR, ifr)
			except Exception as e:
				common.internal_print("Something went wrong with setting up the interface.", -1)
				print e
				sys.exit(-1)

			# adding new route for forwarding packets properly.
			integer_ip = struct.unpack(">I", socket.inet_pton(socket.AF_INET, serverip))[0]
			rangeip = socket.inet_ntop(socket.AF_INET, struct.pack(">I", integer_ip & ((2**int(netmask))-1)<<32-int(netmask)))
			ps = subprocess.Popen(["route", "add", "-net", rangeip+"/"+netmask, serverip], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()
			if stderr:
				if not "File exists" in stderr:
					common.internal_print("Error: adding client route: {0}".format(stderr), -1)
					sys.exit(-1)


		return

	# closing tunnel file descriptor
	def close_tunnel(self, tun):
		try:
			os.close(tun)
		except:
			pass

		return

	# check if more than one or no default route is present
	def check_default_route(self):
	 	if len(self.ip.get_default_routes()) < 1:
			common.internal_print("No default route. Please set up your routing before executing the tool", -1)
			sys.exit(-1)
	 	if len(self.ip.get_default_routes()) > 1:
			common.internal_print("More than one default route. This should be reviewed before executing the tool.", -1)
			sys.exit(-1)	

	# automatic routing set up.
	# check for multiple default routes, if there are then print error message
	# - save default route address
	# - delete default route
	# - add default route, route all packets into the XFLTReaT interface
	# - last route: server IP address routed over the original default route
	def set_default_route(self, serverip, clientip, ip):
		#TODO tunnel thru a tunnel
		if self.os_type == common.OS_LINUX:
			found = False
			routes = self.ip.get_routes()

			self.check_default_route()
			# looking for the the remote server in the route table
			for attrs in self.ip.get_default_routes()[0]['attrs']:
				if attrs[0] == "RTA_GATEWAY":
					self.orig_default_gw = attrs[1]

			for r in routes:
				i = -1
				j = -1
				for a in range(0, len(r["attrs"])):
					if r["attrs"][a][0] == "RTA_DST":
						i = a
					if r["attrs"][a][0] == "RTA_GATEWAY":
						j = a
				if (i > -1) and (j > -1):
					if (r["attrs"][i][1] == serverip) and (r["attrs"][j][1] == self.orig_default_gw):
						# remote server route was already added
						found = True

			self.ip.route('delete', gateway=self.orig_default_gw, dst="0.0.0.0")
			self.ip.route('add', gateway=ip, dst="0.0.0.0")
			if not found:
				# remote server route was not in the table, adding to it
				try:
					self.ip.route('add', gateway=self.orig_default_gw, dst=serverip, mask=32)
				except:
					common.internal_print("Error: Something is not quite right with your route table. Please check.", -1)
					sys.exit(-1)

		if self.os_type == common.OS_MACOSX:
			# https://developer.apple.com/documentation/kernel/rt_msghdr?language=objc
			# s = socket(PF_ROUTE, SOCK_RAW, 0)
			# not sure which is the better way, calling external tools like
			# 'route' or implementing the messaging...

			# get default gateway
			ps = subprocess.Popen(["route", "-n", "get", "default"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()

			# is there a default gateway entry?
			if "not in table" in stderr:
				common.internal_print("No default route. Please set up your routing before executing the tool", -1)
				sys.exit(-1)

			self.orig_default_gw = stdout.split("gateway: ")[1].split("\n")[0]

			# is it an ipv4 address?
			if not common.is_ipv4(self.orig_default_gw):
				common.internal_print("Default gateway is not an IPv4 address.", -1)
				sys.exit(-1)

			ps = subprocess.Popen(["route", "add", "-net", serverip, self.orig_default_gw, "255.255.255.255"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()
			if stderr:
				if not "File exists" in stderr:
					common.internal_print("Error: adding server route: {0}".format(stderr), -1)
					sys.exit(-1)

			ps = subprocess.Popen(["route", "delete", "default"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()
			if stderr:
				common.internal_print("Error: deleting default route: {0}".format(stderr), -1)
				sys.exit(-1)

			ps = subprocess.Popen(["route", "add", "default", ip], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()
			if stderr:
				common.internal_print("Error: adding new default route: {0}".format(stderr), -1)
				sys.exit(-1)

			ps = subprocess.Popen(["route", "add", "-net", clientip, serverip, "255.255.255.255"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()
			if stderr:
				if not "File exists" in stderr:
					common.internal_print("Error: adding new route: {0}".format(stderr), -1)
					sys.exit(-1)
		
		return

	# setting up intermediate route
	# when the module needs an intermediate hop (DNS server, Proxy server)
	# then all encapsulated packet should be sent to the intermediate server
	# instead of the XFLTReaT server
	def set_intermediate_route(self, serverip, proxyip):
		common.internal_print("Changing route table for intermediate hop")
		if self.os_type == common.OS_LINUX:
			self.ip.route('delete', gateway=self.orig_default_gw, dst=serverip, mask=32)
			self.ip.route('add', gateway=self.orig_default_gw, dst=proxyip, mask=32)

		if self.os_type == common.OS_MACOSX:
			ps = subprocess.Popen(["route", "delete", serverip, self.orig_default_gw], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()
			if stderr:
				common.internal_print("Error: delete old route: {0}".format(stderr), -1)
				sys.exit(-1)

			ps = subprocess.Popen(["route", "add", "-net", proxyip, self.orig_default_gw, "255.255.255.255"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()
			if stderr:
				if not "File exists" in stderr:
					common.internal_print("Error: adding server route: {0}".format(stderr), -1)
					sys.exit(-1)
		return

	# restoring default route
	def restore_routes(self, serverip, clientip, ip):
		common.internal_print("Restoring default route")
		if self.os_type == common.OS_LINUX:
			self.ip.route('delete', gateway=self.orig_default_gw, dst=serverip, mask=32)
			self.ip.route('add', gateway=self.orig_default_gw, dst="0.0.0.0")

		if self.os_type == common.OS_MACOSX:
			ps = subprocess.Popen(["route", "delete", serverip, self.orig_default_gw], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()
			if stderr:
				common.internal_print("Error: delete old route: {0}".format(stderr), -1)
				sys.exit(-1)

			ps = subprocess.Popen(["route", "delete", clientip, serverip], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()
			if stderr:
				common.internal_print("Error: delete old server route: {0}".format(stderr), -1)
				sys.exit(-1)

			ps = subprocess.Popen(["route", "delete", "default"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()
			if stderr:
				if not "not in table" in stderr:
					common.internal_print("Error: deleting default route: {0}".format(stderr), -1)
					sys.exit(-1)

			ps = subprocess.Popen(["route", "add", "default", self.orig_default_gw], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			(stdout, stderr) = ps.communicate()
			if stderr:
				if not "File exists" in stderr:
					common.internal_print("Error: adding server route: {0}".format(stderr), -1)
					sys.exit(-1)

		return
