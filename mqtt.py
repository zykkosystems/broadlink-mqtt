#!/usr/bin/env python

import paho.mqtt.client as paho  # pip install paho-mqtt
import broadlink  # pip install broadlink
import os
import sys
import time
import logging
import logging.config
import socket
import sched
from threading import Thread
from test import TestDevice

# read initial config files
dirname = os.path.dirname(os.path.abspath(__file__)) + '/'
CONFIG = os.getenv('BROADLINKMQTTCONFIG', dirname + 'mqtt.conf')
logger = logging.getLogger()
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class Config(object):
    def __init__(self, filename=CONFIG):
        self.config = {}
        execfile(filename, self.config)

    def get(self, key, default='special empty value'):
        v = self.config.get(key, default)
        if v == 'special empty value':
            print("Configuration parameter '%s' should be specified" % key)
            sys.exit(2)
        return v


try:
    cf = Config()
except Exception, e:
    print "Cannot load configuration from file %s: %s" % (CONFIG, str(e))
    sys.exit(2)

qos = cf.get('mqtt_qos', 0)
retain = cf.get('mqtt_retain', False)

topic_prefix = cf.get('mqtt_topic_prefix', 'broadlink/')
#Logging
LOG_LEVEL = logging.DEBUG
LOG_FILE = "{0}broadlink.log".format(cf.get('logdir'))
log_handler = logging.handlers.WatchedFileHandler(LOG_FILE)
formatter = logging.Formatter(LOG_FORMAT)
log_handler.setLevel(logging.DEBUG)
log_handler.setFormatter(formatter)
logger.addHandler(log_handler)
logging.info("logging complete")
logging.debug("logging complete")

print LOG_FILE
#logging.basicConfig(filename=LOG_FILE, format=LOG_FORMAT, level=LOG_LEVEL)


# noinspection PyUnusedLocal
def on_message(client, device, msg):
    command = msg.topic[len(topic_prefix):]

    if command == 'temperature':  # internal notification
        return

    try:
        action = str(msg.payload)
        logging.debug("Received MQTT message " + msg.topic + " " + action)

        if type(device) is dict:
            try:
                devicename = ""
                #assume multi client - so we need to get the client from the first section of the message
                items = command.split("/")
                devicename = items[0]
                del items[0]
                if not devicename:
                    return
                command = '/'.join(map(str, items)) 
                logging.debug("Looking for device of name %s" % devicename)
                device = device[devicename]
            except Exception:
                logging.warning("Could not find device in dictionary")
                for deviceitem in device:
                     logging.warning("Use {0} to access device".format(deviceitem))
            

        if command == 'power':
            if device.type == 'SP1' or device.type == 'SP2':
                state = action == 'on'
                logging.debug("Setting power state to {0}".format(state))
                device.set_power(1 if state else 0)
                return

            if device.type == 'MP1':
                parts = action.split("/", 2)
                if len(parts) == 2:
                    sid = int(parts[0])
                    state = parts[1] == 'on'
                    logging.debug("Setting power state of socket {0} to {1}".format(sid, state))
                    device.set_power(sid, state)
                    return

        if device.type == 'RM2':
            file = dirname + "commands/" + command

            if action == '' or action == 'auto':
                record_or_replay(device, file)
                return
            elif action == 'record':
                record(device, file)
                return
            elif action == 'replay':
                replay(device, file)
                return
            elif action == 'macro':
                file = dirname + "macros/" + command
                macro(device, file)
                return

        logging.debug("Unrecognized MQTT message " + action)
    except Exception:
        logging.exception("Error")


# noinspection PyUnusedLocal
def on_connect(client, device, flags, result_code):
    topic = topic_prefix + '#'
    logging.debug("Connected to MQTT broker, subscribing to topic " + topic)
    mqttc.subscribe(topic, qos)


# noinspection PyUnusedLocal
def on_disconnect(client, device, rc):
    logging.debug("OOOOPS! Broadlink disconnects")
    time.sleep(10)


def record_or_replay(device, file):
    if os.path.isfile(file):
        replay(device, file)
    else:
        record(device, file)


def record(device, file):
    logging.debug("Recording command to file " + file)
    # receive packet
    device.enter_learning()
    ir_packet = None
    attempt = 0
    while ir_packet is None and attempt < 6:
        time.sleep(5)
        ir_packet = device.check_data()
        attempt = attempt + 1
    if ir_packet is not None:
        # write to file
        directory = os.path.dirname(file)
        if not os.path.exists(directory):
            os.makedirs(directory)
        with open(file, 'wb') as f:
            f.write(str(ir_packet).encode('hex'))
        logging.debug("Done")
    else:
        logging.warn("No command received")


def replay(device, file):
    logging.debug("Replaying command from file " + file)
    with open(file, 'rb') as f:
        ir_packet = f.read()
    device.send_data(ir_packet.decode('hex'))


def macro(device, file):
    logging.debug("Replaying macro from file " + file)
    with open(file, 'rb') as f:
        for line in f:
            line = line.strip(' \n\r\t')
            if len(line) == 0 or line.startswith("#"):
                continue
            if line.startswith("pause "):
                pause = int(line[6:].strip())
                logging.debug("Pause for " + str(pause) + " milliseconds")
                time.sleep(pause / 1000.0)
            else:
                command_file = dirname + "commands/" + line
                replay(device, command_file)


def get_device(cf):
    device_type = cf.get('device_type', 'lookup')
    if device_type == 'lookup':
        local_address = cf.get('local_address', None)
        lookup_timeout = cf.get('lookup_timeout', 20)
        devices = broadlink.discover(timeout=lookup_timeout) if local_address is None else \
            broadlink.discover(timeout=lookup_timeout, local_ip_address=local_address)
        if len(devices) == 0:
            logging.error('No Broadlink device found')
            sys.exit(2)
        if len(devices) > 1:
            logging.error('More than one Broadlink device found (' + ', '.join([d.host for d in devices]) + ')')
            sys.exit(2)
        return devices[0]
    elif device_type == 'test':
        return TestDevice(cf)
    else:
        host = (cf.get('device_host'), 80)
        mac = bytearray.fromhex(cf.get('device_mac').replace(':', ' '))
        if device_type == 'rm':
            return broadlink.rm(host=host, mac=mac)
        elif device_type == 'sp1':
            return broadlink.sp1(host=host, mac=mac)
        elif device_type == 'sp2':
            return broadlink.sp2(host=host, mac=mac)
        elif device_type == 'a1':
            return broadlink.a1(host=host, mac=mac)
        elif device_type == 'mp1':
            return broadlink.mp1(host=host, mac=mac)
        else:
            logging.error('Incorrect device configured: ' + device_type)
            sys.exit(2)

def get_devices(cf):
    devicesdict = {}
    device_type = cf.get('device_type', 'lookup')
    if device_type == 'lookup':
        #can't test this as for some reason dicovery is not working for me
        local_address = cf.get('local_address', None)
        lookup_timeout = cf.get('lookup_timeout', 20)
        devices = broadlink.discover(timeout=lookup_timeout) if local_address is None else \
            broadlink.discover(timeout=lookup_timeout, local_ip_address=local_address)
        if len(devices) == 0:
            logging.error('No Broadlink device found')
            sys.exit(2)
        if len(devices) > 1:
            logging.error('More than one Broadlink device found (' + ', '.join([d.host for d in devices]) + ')')
        
        lookupdevices = {}
        for device in devices:
            logging.debug("Adding device with name : {0}.  If you want to message it the format is {1}/{0}/command".format(device.mac,topic_prefix))
            devicename = device.max.replace(':','')
            devicesdict[device.mac] = device

        return devicesdict
    elif device_type == 'test':
        return TestDevice(cf)
    elif device_type == 'list':
        counter = 1
        while True:
            hoststr = cf.get('device_host_{0}'.format(counter),'')
            if hoststr is None or hoststr == '':
                break
            host = (hoststr,80)
            
            macstring = cf.get('device_mac_{0}'.format(counter))
            mac = bytearray.fromhex(macstring.replace(':', ' '))
            device_type = cf.get('device_type_{0}'.format(counter))
            devicename = macstring.replace(':','')
            logging.info('Found device host:{0} mac:{1} type:{2} as key:{3}'.format(host,macstring,device_type,devicename))

            if device_type == 'rm':
                devicesdict[devicename] = broadlink.rm(host=host, mac=mac)
            elif device_type == 'sp1':
                devicesdict[devicename] = broadlink.sp1(host=host, mac=mac)
            elif device_type == 'sp2':
               devicesdict[devicename] = broadlink.sp2(host=host, mac=mac)
            elif device_type == 'a1':
               devicesdict[devicename] =  broadlink.a1(host=host, mac=mac)
            elif device_type == 'mp1':
                devicesdict[devicename] = broadlink.mp1(host=host, mac=mac)
            else:
                logging.error('Incorrect device configured: ' + device_type)
                sys.exit(2)
            logging.debug("Adding device with name : {0}.  If you want to message it the format is {1}/{0}/command".format(devicename,topic_prefix))

            counter += 1
        return devicesdict
    else:
        host = (cf.get('device_host'), 80)
        macstring = cf.get('device_mac')
        mac = bytearray.fromhex(macstring.replace(':', ' '))
        if device_type == 'rm':
            devicesdict[macstring] = broadlink.rm(host=host, mac=mac)
        elif device_type == 'sp1':
            devicesdict[macstring] = broadlink.sp1(host=host, mac=mac)
        elif device_type == 'sp2':
            devicesdict[macstring] = broadlink.sp2(host=host, mac=mac)
        elif device_type == 'a1':
            devicesdict[macstring] = broadlink.a1(host=host, mac=mac)
        elif device_type == 'mp1':
            devicesdict[macstring] = broadlink.mp1(host=host, mac=mac)
        else:
            logging.error('Incorrect device configured: ' + device_type)
            sys.exit(2)
        return devicesdict

def broadlink_rm_temperature_timer(scheduler, delay, device):
    scheduler.enter(delay, 1, broadlink_rm_temperature_timer, [scheduler, delay, device])

    try:
        temperature = str(device.check_temperature())
        topic = topic_prefix + "temperature"
        logging.debug("Sending RM temperature " + temperature + " to topic " + topic)
        mqttc.publish(topic, temperature, qos=qos, retain=retain)
    except:
        logging.exception("Error")


class SchedulerThread(Thread):
    def __init__(self, scheduler):
        Thread.__init__(self)
        self.scheduler = scheduler

    def run(self):
        try:
            self.scheduler.run()
        except:
            logging.exception("Error")


if __name__ == '__main__':
    logger = logging.getLogger()
    log_handler2 = logging.StreamHandler()
    log_handler2.setLevel(logging.DEBUG)
    logger.setLevel(logging.DEBUG)
    log_handler2.setFormatter(formatter)
    logger.addHandler(log_handler2)
    logger.addHandler(log_handler)
    logger.info("logging complete")
    logger.error("test")

    device_type = cf.get('device_type', 'lookup')
    #if device_type == 'lookup' or device_type == 'list':
    #    devices = get_devices(cf)
    #else:
    #    devices = {}
    #    devices[0] = get_devices(cf)
    devices = get_devices(cf)

    threads = []
    mqttcs = []

    broadlink_rm_temperature_interval = cf.get('broadlink_rm_temperature_interval', 0)

    clientid = cf.get('mqtt_clientid', 'broadlink-%s' % os.getpid())
    # initialise MQTT broker connection
    #mqttc = paho.Client(clientid, clean_session=cf.get('mqtt_clean_session', False), userdata=device)
    
    mqttc = None
    for device in devices.itervalues():
        logging.debug('Connected to %s Broadlink device at %s' % (device.type, device.host))
        device.auth()
        if device.type == 'RM2' and broadlink_rm_temperature_interval > 0:
            scheduler = sched.scheduler(time.time, time.sleep)
            scheduler.enter(broadlink_rm_temperature_interval, 1, broadlink_rm_temperature_timer,
                            [scheduler, broadlink_rm_temperature_interval, device])
            # scheduler.run()
            tt = SchedulerThread(scheduler)
            tt.daemon = True
            tt.start()
            threads.append(tt)
            
    if len(devices) == 1:
        mqttc = paho.Client(clientid, clean_session=cf.get('mqtt_clean_session', False), userdata=devices.values()[0])
    else:
        mqttc = paho.Client(clientid, clean_session=cf.get('mqtt_clean_session', False), userdata=devices)

    mqttc.on_message = on_message
    mqttc.on_connect = on_connect
    mqttc.on_disconnect = on_disconnect

    mqttc.will_set('clients/broadlink', payload="Adios!", qos=0, retain=False)

    # Delays will be: 3, 6, 12, 24, 30, 30, ...
    # mqttc.reconnect_delay_set(delay=3, delay_max=30, exponential_backoff=True)

    mqttc.username_pw_set(cf.get('mqtt_username'), cf.get('mqtt_password'))
    mqttc.connect(cf.get('mqtt_broker', 'localhost'), int(cf.get('mqtt_port', '1883')), 60)

    while True:
        try:
            mqttc.loop_forever()
        except socket.error:
            time.sleep(5)
        except KeyboardInterrupt:
            sys.exit(0)
        except:
            logging.exception("Error")


