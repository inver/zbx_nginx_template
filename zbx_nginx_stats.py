#!/usr/bin/python

import urllib2, base64, re, struct, time, socket, sys, datetime, os.path

try:
    import json
except:
    import simplejson as json

import config

class Metric(object):
    def __init__(self, host, key, value, clock=None):
        self.host = host
        self.key = key
        self.value = value
        self.clock = clock

    def __repr__(self):
        if self.clock is None:
            return 'Metric(%r, %r, %r)' % (self.host, self.key, self.value)
        return 'Metric(%r, %r, %r, %r)' % (self.host, self.key, self.value, self.clock)

def send_to_zabbix(metrics, zabbix_host='127.0.0.1', zabbix_port=10051):
    
    j = json.dumps
    metrics_data = []
    for m in metrics:
        clock = m.clock or ('%d' % time.time())
        metrics_data.append(('{"host":%s,"key":%s,"value":%s,"clock":%s}') % (j(m.host), j(m.key), j(m.value), j(clock)))
    json_data = ('{"request":"sender data","data":[%s]}') % (','.join(metrics_data))
    data_len = struct.pack('<Q', len(json_data))
    packet = 'ZBXD\x01'+ data_len + json_data
    
    #print packet
    #print ':'.join(x.encode('hex') for x in packet)

    try:
        zabbix = socket.socket()
        zabbix.connect((zabbix_host, zabbix_port))
        zabbix.sendall(packet)
        resp_hdr = _recv_all(zabbix, 13)
        if not resp_hdr.startswith('ZBXD\x01') or len(resp_hdr) != 13:
            print 'Wrong zabbix response'
            return False
        resp_body_len = struct.unpack('<Q', resp_hdr[5:])[0]
        resp_body = zabbix.recv(resp_body_len)
        zabbix.close()

        resp = json.loads(resp_body)
        #print resp
        if resp.get('response') != 'success':
            print 'Got error from Zabbix: %s' % resp
            return False
        return True
    except:
        print 'Error while sending data to Zabbix'
        return False


def _recv_all(sock, count):
    buf = ''
    while len(buf)<count:
        chunk = sock.recv(count-len(buf))
        if not chunk:
            return buf
        buf += chunk
    return buf


def get(url, login, passwd):
	req = urllib2.Request(url)
	if login and passwd:
		base64string = base64.encodestring('%s:%s' % (login, passwd)).replace('\n', '')
		req.add_header("Authorization", "Basic %s" % base64string)   
	q = urllib2.urlopen(req)
	res = q.read()
	q.close()
	return res

def parse_nginx_stat(data):
	a = {}
	# Active connections
	a['active_connections'] = re.match(r'(.*):\s(\d*)', data[0], re.M | re.I).group(2)
	# Accepts
	a['accepted_connections'] = re.match(r'\s(\d*)\s(\d*)\s(\d*)', data[2], re.M | re.I).group(1)
	# Handled
	a['handled_connections'] = re.match(r'\s(\d*)\s(\d*)\s(\d*)', data[2], re.M | re.I).group(2)
	# Requests
	a['handled_requests'] = re.match(r'\s(\d*)\s(\d*)\s(\d*)', data[2], re.M | re.I).group(3)
	# Reading
	a['header_reading'] = re.match(r'(.*):\s(\d*)(.*):\s(\d*)(.*):\s(\d*)', data[3], re.M | re.I).group(2)
	# Writing
	a['body_reading'] = re.match(r'(.*):\s(\d*)(.*):\s(\d*)(.*):\s(\d*)', data[3], re.M | re.I).group(4)
	# Waiting
	a['keepalive_connections'] = re.match(r'(.*):\s(\d*)(.*):\s(\d*)(.*):\s(\d*)', data[3], re.M | re.I).group(6)
	return a


def read_seek(file):
    if os.path.isfile(file):
        f = open(file, 'r')
        try:
            result = int(f.readline())
            f.close()
            return result
        except:
            return 0
    else:
        return 0

def write_seek(file, value):
    f = open(file, 'w')
    f.write(value)
    f.close()


#print '[12/Mar/2014:03:21:13 +0400]'

d = datetime.datetime.now()-datetime.timedelta(minutes=config.time_delta)
minute = int(time.mktime(d.timetuple()) / 60)*60
d = d.strftime('%d/%b/%Y:%H:%M')

total_rps = 0
average_response = 0.0;
rps = [0]*60
tps = [0]*60
res_code = {}
res_time_list = []

nf = open(config.nginx_log_file_path, 'r')

new_seek = seek = read_seek(config.seek_file)

# if new log file, don't do seek
if os.path.getsize(config.nginx_log_file_path) > seek:
    nf.seek(seek)

line = nf.readline()
while line:
    if d in line:
        new_seek = nf.tell()
        total_rps += 1
        sec = int(re.match('(.*):(\d+):(\d+):(\d+)\s', line).group(4))
        code = re.match(r'(.*)"\s(\d*)\s', line).group(2)
        c = re.match(r'(.*)"\s(\d*)\s(\d*).(\d*)', line).group(3) + '.' + re.match(r'(.*)"\s(\d*)\s(\d*).(\d*)', line).group(4)
        res_time = float(c)
        if (res_time > 0.0):
            res_time_list.append(res_time)

        if code in res_code:
            res_code[code] += 1
        else:
            res_code[code] = 1

        rps[sec] += 1
    line = nf.readline()

if total_rps != 0:
    write_seek(config.seek_file, str(new_seek))

nf.close()

if len(res_time_list) > 0:
    average_response = sum(res_time_list) / len(res_time_list);

metric = (len(sys.argv) >= 2) and re.match(r'nginx\[(.*)\]', sys.argv[1], re.M | re.I).group(1) or False
data = get(config.stat_url, config.username, config.password).split('\n')
data = parse_nginx_stat(data)

data_to_send = []

# Adding the metrics to response
if not metric:
    for i in data:
        data_to_send.append(Metric(config.hostname, ('nginx[%s]' % i), data[i]))
else:
    print data[metric]

# Adding the request per seconds to response
for t in range(0,60):
    data_to_send.append(Metric(config.hostname, 'nginx[rps]', rps[t], minute+t))

# Adding the response codes stats to respons
for t in res_code:
    data_to_send.append(Metric(config.hostname, ('nginx[%s]' % t), res_code[t]))

data_to_send.append(Metric(config.hostname, 'nginx[average_response]', average_response))

send_to_zabbix(data_to_send, config.zabbix_host, config.zabbix_port)