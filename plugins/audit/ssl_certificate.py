'''
ssl_certificate.py

Copyright 2006 Andres Riancho

This file is part of w3af, w3af.sourceforge.net .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

'''
import socket
import ssl
import re
import os

from time import gmtime
from datetime import date
from pprint import pformat

import core.controllers.outputManager as om
import core.data.kb.knowledgeBase as kb
import core.data.kb.info as info
import core.data.kb.vuln as vuln
import core.data.constants.severity as severity

from core.controllers.plugins.audit_plugin import AuditPlugin
from core.data.options.opt_factory import opt_factory
from core.data.options.option_types import INPUT_FILE
from core.data.options.option_list import OptionList
from core.data.bloomfilter.bloomfilter import scalable_bloomfilter


class ssl_certificate(AuditPlugin):
    '''
    Check the SSL certificate validity (if https is being used).

    @author: Andres Riancho (andres.riancho@gmail.com)
    @author: Taras ( oxdef@oxdef.info )
    '''

    def __init__(self):
        AuditPlugin.__init__(self)
        
        self._already_tested = scalable_bloomfilter()
        self._min_expire_days = 30
        self._ca_file = os.path.join('plugins','audit','ssl_certificate','ca.pem')

    def audit(self, freq):
        '''
        Get the cert and do some checks against it.

        @param freq: A FuzzableRequest
        '''
        url = freq.getURL()
        domain = url.getDomain()
        
        if 'HTTPS' != url.getProtocol().upper() or domain in self._already_tested:
            return
        
        self._already_tested.add(domain)

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SSLv2 check
        # NB! From OpenSSL lib ver >= 1.0 there is no support for SSLv2
        try:
            ssl_sock = ssl.wrap_socket(s,
                                       cert_reqs=ssl.CERT_NONE,
                                       ssl_version=ssl.PROTOCOL_SSLv2)
            ssl_sock.connect((domain, url.getPort()))
        except Exception, e:
            pass
        else:
            v = vuln.vuln()
            v.setPluginName(self.get_name())
            v.setURL(url)
            v.setSeverity(severity.LOW)
            v.set_name('Insecure SSL version')
            desc = 'The target host "%s" has SSL version 2 enabled which is'
            desc += ' known to be insecure.'
            v.set_desc(desc % domain)
            kb.kb.append(self, 'ssl_v2', v)
            om.out.vulnerability(desc % domain)

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            ssl_sock = ssl.wrap_socket(s,
                                       ca_certs=self._ca_file,
                                       cert_reqs=ssl.CERT_REQUIRED,
                                       ssl_version=ssl.PROTOCOL_SSLv23)
            ssl_sock.connect((domain, url.getPort()))
            match_hostname(ssl_sock.getpeercert(), domain)
        except (ssl.SSLError, CertificateError), e:
            invalid_cert = isinstance(e, CertificateError)
            details = str(e)

            if isinstance(e, ssl.SSLError):
                err_chunks = details.split(':')
                if len(err_chunks) == 7:
                    details = err_chunks[5] + ':' + err_chunks[6]
                if 'CERTIFICATE' in details:
                    invalid_cert = True
           
            if invalid_cert:
                v = vuln.vuln()
                v.setSeverity(severity.LOW)
                v.set_name('Invalid SSL certificate')
                desc = '"%s" uses an invalid security certificate. '
                desc += 'The certificate is not trusted because: "%s".'
                tag = 'invalid_ssl_cert'
            else:
                # We use here info() instead of vuln() because it is too common case
                v = info.info()
                v.set_name('Invalid SSL connection')
                desc = '"%s" has an invalid SSL configuration. Technical details: "%s"'
                tag = 'invalid_ssl_connect'

            v.set_desc(desc % (domain, details))
            v.setPluginName(self.get_name())
            v.setURL(url)
            kb.kb.append(self, tag, v)
            om.out.vulnerability(v.get_name() + ': ' + v.get_desc())
            return

        except Exception, e:
            om.out.debug(str(e))
            return
        
        cert = ssl_sock.getpeercert()
        cert_der = ssl_sock.getpeercert(binary_form=True)
        cipher = ssl_sock.cipher()
        ssl_sock.close()

        exp_date = gmtime(ssl.cert_time_to_seconds(cert['notAfter']))
        expire_days = (date(exp_date.tm_year, exp_date.tm_mon, exp_date.tm_mday) - date.today()).days
        if expire_days < self._min_expire_days:
            i = info.info()
            i.setURL(url)
            i.setPluginName(self.get_name())
            i.set_name('Soon expire SSL certificate')
            i.set_desc('The certificate for "%s" will expire soon.' % domain)
            kb.kb.append(self, 'ssl_soon_expire', i) 
            om.out.information(i.get_desc())

        # Print the SSL information to the log
        desc = 'This is the information about the SSL certificate used in the target site:\n'
        desc += self._dump_ssl_info(cert, cert_der, cipher)
        om.out.information(desc)
        i = info.info()
        i.setURL(url)
        i.setPluginName(self.get_name())
        i.set_name('SSL Certificate')
        i.set_desc(desc)
        kb.kb.append(self, 'certificate', i)


    def _dump_ssl_info(self, cert, cert_der, cipher):
        '''Dump X509 certificate.'''

        res = '\n== Certificate information ==\n'
        res += pformat(cert)
        res += '\n\n== Used cipher ==\n' + pformat(cipher)
        res += '\n\n== Certificate dump ==\n' + ssl.DER_cert_to_PEM_cert(cert_der)
        # Indent
        res = res.replace('\n', '\n    ')
        res = '    ' + res
        return res

    def get_options(self):
        '''
        @return: A list of option objects for this plugin.
        '''
        ol = OptionList()

        d = 'Set minimal amount of days before expiration of the certificate for alerting'
        h = 'If the certificate will expire in period of minExpireDays w3af will show alert about it'
        o = opt_factory('minExpireDays', self._min_expire_days, d, 'integer', help=h)
        ol.add(o)

        d = 'Set minimal amount of days before expiration of the certificate for alerting'
        h = 'CA PEM file path'
        o = opt_factory('caFileName', self._ca_file, d, INPUT_FILE, help=h)
        ol.add(o)

        return ol

    def set_options(self, options_list):
        '''
        This method sets all the options that are configured using the user interface 
        generated by the framework using the result of get_options().

        @parameter OptionList: A dictionary with the options for the plugin.
        @return: No value is returned.
        '''
        self._min_expire_days = options_list['minExpireDays'].get_value()
        self._ca_file = options_list['caFileName'].get_value()

    def get_long_desc(self):
        '''
        @return: A DETAILED description of the plugin functions and features.
        '''
        return '''
        This plugin audits SSL certificate parameters.
        
        One configurable parameter exists:
            - minExpireDays
            - CA PEM file path
         
        Note: It's only usefull when testing HTTPS sites.
        '''

# 
# This code taken from
# http://pypi.python.org/pypi/backports.ssl_match_hostname/
#
class CertificateError(Exception):
    pass

def _dnsname_to_pat(dn):
    pats = []
    for frag in dn.split(r'.'):
        if frag == '*':
            # When '*' is a fragment by itself, it matches a non-empty dotless
            # fragment.
            pats.append('[^.]+')
        else:
            # Otherwise, '*' matches any dotless fragment.
            frag = re.escape(frag)
            pats.append(frag.replace(r'\*', '[^.]*'))
    return re.compile(r'\A' + r'\.'.join(pats) + r'\Z', re.IGNORECASE)

def match_hostname(cert, hostname):
    """Verify that *cert* (in decoded format as returned by
    SSLSocket.getpeercert()) matches the *hostname*.  RFC 2818 rules
    are mostly followed, but IP addresses are not accepted for *hostname*.

    CertificateError is raised on failure. On success, the function
    returns nothing.
    """
    if not cert:
        raise ValueError("empty or no certificate")
    dnsnames = []
    san = cert.get('subjectAltName', ())
    for key, value in san:
        if key == 'DNS':
            if _dnsname_to_pat(value).match(hostname):
                return
            dnsnames.append(value)
    if not dnsnames:
        # The subject is only checked when there is no dNSName entry
        # in subjectAltName
        for sub in cert.get('subject', ()):
            for key, value in sub:
                # XXX according to RFC 2818, the most specific Common Name
                # must be used.
                if key == 'commonName':
                    if _dnsname_to_pat(value).match(hostname):
                        return
                    dnsnames.append(value)
    if len(dnsnames) > 1:
        raise CertificateError("hostname %s "
            "doesn't match either of %s"
            % (hostname, ', '.join(map(str, dnsnames))))
    elif len(dnsnames) == 1:
        raise CertificateError("hostname %s "
            "doesn't match %s"
            % (hostname, dnsnames[0]))
    else:
        raise CertificateError("no appropriate commonName or "
            "subjectAltName fields were found")
