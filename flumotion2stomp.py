#!/usr/bin/python
# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4

# (C) Copyright 2007 Zaheer Abbas Merali <zaheerabbas at merali dot org>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

import sys
import copy
import os
root = os.path.join('/usr/lib', 'flumotion', 'python')
sys.path.insert(0, root)

from stompservice import StompClientFactory
from orbited import json

from flumotion.component import feed
from twisted.internet import reactor, defer
from twisted.internet.task import LoopingCall
from flumotion.twisted import pb, flavors
from flumotion.common import log, errors
from flumotion.common.planet import moods
from flumotion.admin import connections
from flumotion.admin.command import utils
from flumotion.admin.admin import AdminModel
from flumotion.monitor.nagios import util
from zope.interface import implements
import optparse

# register the unjellyable
from flumotion.common import componentui

class FluToStomp:

    implements(flavors.IStateListener)

    def __init__(self, args):
        self._components = []
        self.uistates = {}
        log.init()

        parser = optparse.OptionParser()
        parser.add_option('-d', '--debug',
                          action="store", type="string", dest="debug",
                          help="set debug levels")
        parser.add_option('-u', '--usage',
                          action="store_true", dest="usage",
                          help="show a usage message")
        parser.add_option('-m', '--manager',
                          action="store", type="string", dest="manager",
                          help="the manager to connect to, e.g. localhost:7531")
        parser.add_option('', '--no-ssl',
                          action="store_true", dest="no_ssl",
                          help="disable encryption when connecting to the manager")
        parser.add_option('-s', '--stomp-port', action="store", type="string",
                          dest="stomp")
        options, args = parser.parse_args(args)

        if options.debug:
            log.setFluDebug(options.debug)

        if options.usage:
            self.usage(args)

        if not options.manager or not options.stomp:
            self.usage(args)
        
        print "need to connect to stomp port %s" % (options.stomp,)
        self.options = options
        connection = connections.parsePBConnectionInfo(options.manager,
                                                       not options.no_ssl)
        self.model = model = AdminModel()
        d = model.connectToManager(connection)

        def failed(failure):
            if failure.check(errors.ConnectionRefusedError):
                print "Manager refused connection. Check your user and password."
            elif failure.check(errors.ConnectionFailedError):
                message = "".join(failure.value.args)
                print "Connection to manager failed: %s" % message
            else:
                print ("Exception while connecting to manager: %s"
                       % log.getFailureMessage(failure))
            return failure

        d.addErrback(failed)
        d.addErrback(lambda x: reactor.stop())
        d.addCallback(self.manager_connected)

    @defer.inlineCallbacks
    def manager_connected(self, model):
        try:
            psd = model.callRemote('getPlanetState')
            yield psd
            planet = psd.result
            self.planet = planet
            flows = planet.get('flows')
            flow = flows[0]
            for f in flows:
               if f.get('name') == 'default':
                  flow = f
                  break
            self._components = flow.get('components')
            self.stomp_client = StompClient()
            reactor.connectTCP("localhost", int(self.options.stomp), self.stomp_client)

            flow.addListener(self, append=self.flow_state_append, remove=self.flow_state_remove)
            for c in self._components:
                self.new_component(c)
            
        except Exception, e:
            print log.getExceptionMessage(e)

    def usage(self, args, exitval=0):
        print "usage: %s [OPTIONS] -m MANAGER " \
            "-s STOMPPORT" % args[0]
        print ''
        print 'See %s -h for help on the available options.' % args[0]
        sys.exit(exitval)

    def components(self):
        l = []
        for c in self._components:
            keys = c.keys()
            d = {}
            for k in keys:
                if k == 'parent':
                    continue
                elif k == 'messages':
                    messages = []
                    for m in c.get('messages'):
                        messages.append(str(m))
                    d['messages'] = messages
                else:
                    d[k] = c.get(k)
            l.append(d)
        return l

    def parse_component(self, state):
        component = {}
        for k in state.keys():
            if k == 'parent':
                continue
            elif k == 'messages':
                messages = []
                for m in state.get('messages'):
                    messages.append(str(m))
                component['messages'] = messages
            else:
                component[k] = state.get(k)
        return component

    def component_state_set(self, state, key, value):
        component = self.parse_component(state)
        print "component changed %r" % (component,)
        self.stomp_client.send_changes({ "action": "change", "component": component })

    def flow_state_append(self, state, key, value):
        if key == 'components':
           component = self.parse_component(value)
           self.new_component(value)

           self.stomp_client.send_changes({ "action": "add", "component": component })

    def flow_state_remove(self, state, key, value):
        if key == 'components':
           component = value.get('name')
           self.stomp_client.send_changes({ "action": "remove", "component": component }) 
    
    def new_component(self, state):
        state.addListener(self, set_= self.component_state_set)

    @defer.inlineCallbacks
    def poll_uistate(self, component):
        state = None
        for c in self._components:
            if c.get('name') == component:
               state = c
               break
        if not state:
           defer.returnValue(False)
        d = self.model.componentCallRemote(state, "getUIState")
        try:
           yield d
           uistate = d.result
           self.uistates[uistate] = state.get('name')
           parsed = self.parse_uistate(uistate)
           for key in parsed:
               self.stomp_client.send_uistate(state.get('name'), key, parsed[key])
           uistate.addListener(self, set_ = self.uistate_set, append=self.uistate_set)
        except Exception, e:
           print "got error %r getting uistate from component %r" % (e,state)

    def uistate_set(self, state, key, value):
        component = self.uistates[state]
        data = value
        if hasattr(value, "append"):
            data = []
            for item in value:
                data.append(self.parse_uistate(item))
        self.stomp_client.send_uistate(component, key, data)

    def parse_uistate(self, state):
        component = {}
        if not hasattr(state, 'keys'):
            return state
        for k in state.keys():
            if k == 'parent':
                continue
            elif k == 'feeders' or k == 'eaters' or k == 'clients':
                component[k] = []
                for e in state.get(k):
                    component[k].append(self.parse_uistate(e))
            else:
                component[k] = state.get(k)
        return component

 
class StompClient(StompClientFactory):
    status = {}
    def recv_connected(self, msg):
        print "Connected with stomp"
        self.timer = LoopingCall(self.send_status)
        self.timer.start(5)
        self.subscribe("/flumotion/poll")
        
    def recv_message(self, msg):
        print "Message received %r" % (msg,)
        try:
            component = msg["body"]
            global main
            main.poll_uistate(component)

        except Exception, e:
            print "Broken Total Request %r" % (e,)

    def send_status(self):
        global main
        self.send("/flumotion/components/initial", json.encode(main.components()))

    def send_changes(self, changes):
        self.send("/flumotion/components/changes", json.encode(changes))

    def send_uistate(self, component, key, value):
        try:
           encoded = json.encode(value)
           self.send("/flumotion/components/uistate/%s/%s" % (component, key), encoded)
           print "uistate component %r key %r value %r" % (component, key, value)
        except Exception, e:
           print "Error %r with uistate %r" % (e, value)

main = FluToStomp(sys.argv)
reactor.run()
