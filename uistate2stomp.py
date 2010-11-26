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
import os
root = os.path.join('/usr/lib', 'flumotion', 'python')
sys.path.insert(0, root)

from stompservice import StompClientFactory
from orbited import json

from flumotion.component import feed
from twisted.internet import reactor, defer
from flumotion.twisted import pb
from flumotion.common import log, errors
from flumotion.common.planet import moods
from flumotion.admin import connections
from flumotion.admin.command import utils
from flumotion.admin.admin import AdminModel
from flumotion.monitor.nagios import util

import optparse

class FluToStomp:
    def __init__(self, args):
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

        options, args = parser.parse_args(args)

        if options.debug:
            log.setFluDebug(options.debug)

        if options.usage:
            usage(args)

        if not options.manager:
            usage(args)

        self.options = options
        connection = connections.parsePBConnectionInfo(options.manager,
                                                       not options.no_ssl)
        model = AdminModel()
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
        d.addCallback(lambda x: reactor.stop())

    @defer.inlineCallbacks
    def manager_connected(self, model):
        try:
            psd = model.callRemote('getPlanetState')
            yield psd
            planet = psd.result
            print planet
            flows = planet.get('flows')
            for f in flows:
                components = f.get('components')
                for c in components:
                    print "%s: %s" % (c.get('name'),moods.get(c.get('mood')).name)
        except Exception, e:
            print log.getExceptionMessage(e)

main = FluToStomp(sys.argv)
reactor.run()
