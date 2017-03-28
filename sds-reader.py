#!/usr/bin/env python
from __future__ import division
import serial
import os 
import sys, time
from datetime import datetime, timedelta
import commands
import subprocess
import json
import httplib, urllib
import tempfile
import pickle
import glob
import traceback
import numpy as np
import requests
import threading
import RPi.GPIO as GPIO

#ignore already in use... i know it.. maybe the mode was already set nothing else runs this sytem...
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BOARD)
GPIO.setup(11, GPIO.OUT)
pin = GPIO.PWM(11, 0.5)

INTERVAL = 1
INTERVAL_UPLOAD = 10
STOP_ON_ERRORS = 0
SENSORID = "schuurstof"
USBPORT  = "/dev/ttyS0"
POSTURL = "https://www.kanbeter.info/weer/storedust"

class SDS011Reader:

    def __init__(self, inport):
        self._started = 1
        self.serial = serial.Serial(port=inport,baudrate=9600)
        self.species = []
        self._needsAlarm = .1
        self._needsAlarmLast = 0

    def needsAlarm( self ):
        return self._needsAlarm

    def startAlarm ( self, hz ):
        self._needsAlarm = hz
 
    def stopAlarm( self ):
        self._needsAlarm = 0    

    def started( self ):
        return self._started

    def stop( self ):
        self._started = 0

    def readValue( self ):
        step = 0
        while True: 
            while self.serial.inWaiting()!=0:
                v=ord(self.serial.read())

                if step ==0:
                    if v==170:
                        step=1

                elif step==1:
                    if v==192:
                        values = [0,0,0,0,0,0,0]
                        step=2
                    else:
                        step=0

                elif step>8:
                    step =0
                    pm25 = values[0]+values[1]*256
                    pm10 = values[2]+values[3]*256
                    return [pm25,pm10]

                elif step>=2:
                    values[step-2]=v
                    step= step+1


    def getClear( self ):
        species = self.species
        self.species = []
        return species


    def read( self ):
        start = os.times()[4]

        count = 0
        speciesType = ["pm2.5-mg","pm10-mg"]

        while self._started:
            try:
                values = self.readValue()
                dt = datetime.now().isoformat()
                self.species.append([dt, values[0], values[1]])

                if values[0] >= 2505 and self._needsAlarmLast != 2505: 
                    #hazardous
                    self._needsAlarm = 10
                    self._needsAlarmLast = 2505
                if values[0] >= 1505 and values[0] < 2505 and self._needsAlarmLast != 1505:
                    #very unhealthy
                    self._needsAlarm = 8
                    self._needsAlarmLast = 1505
                if values[0] >= 555 and values[0] < 1505 and self._needsAlarmLast != 555:
                    #unhealthy
                    self._needsAlarm = 6
                    self._needsAlarmLast = 555
                if values[0] >= 355 and values[0] < 555 and self._needsAlarmLast != 355:
                    #unhealthy for sensitive groups
                    self._needsAlarm = 4
                    self._needsAlarmLast = 355
                if values[0] >= 121 and values[0] < 355 and self._needsAlarmLast != 121:
                    #average
                    self._needsAlarm = 1
                    self._needsAlarmLast = 121
                if values[0] < 355:
                    self._needsAlarm = 0
                    self._needsAlarmLast = 0

                count += 1
                #dt = os.times()[4]-start
                print("[{:18}] PM2.5:{:4.1f} PM10:{:4.1f}".format(
                    dt,float(values[0])/10,float(values[1])/10
                    ))
                time.sleep(INTERVAL)
            except:
                e = sys.exc_info()[0]
                e = traceback.format_exc(9);
                print("Can not read the sensor data: "+str(e))
                if(STOP_ON_ERRORS == 1):
                    self._started = 0;
                    sys.exit()
                    

class SensorDataUploader:

    def __init__(self, id):
        self.faildate = 0
        self.writecnt = 0
        self.id = id
        self.filePath = ''
        self.filePath2 = ''

    def httpPost(self,idata):
        try:
            response = requests.post(POSTURL, data={"data": json.dumps({'data': idata})});
            data = response.text;
            print(response.status_code)
            r = data == "1"
            if r!=1:
                print("Server says -> {0} ".format(data))
            return r
        except:
            e = sys.exc_info()[0]
            e = traceback.format_exc(9);

            print("http-post: error. "+str(e))
            return 0 


    def file_get_contents(self,filename):
        with open(filename) as f:
            return f.read()

    def file_put_contents(self,filename,data):
        with open(filename,'w') as f:
            f.write(data)
            f.close()

    def postValues(self,values):
        self.filePath = os.path.join(tempfile.gettempdir(), self.id+".pickle")
        if self.faildate==0:
            self.faildate = time.strftime("%Y-%m-%d-%H-%M-%S")
        self.filePath2 = os.path.join(os.path.dirname(__file__), "pending."+self.id+"."+self.faildate+".pickle")

        if os.path.isfile(self.filePath) and os.path.getsize(self.filePath)>0:
            ovalues = pickle.loads(self.file_get_contents(self.filePath))
            print(ovalues)

            print("previous queue size has "+str(len(ovalues))+" entries")
            values = ovalues + values
            os.remove(self.filePath)
            print(values)

        if not self.httpPost(values):
            n = len(values)
            print("upload not ok... there are now {0} entries pending ({1}).".format(n,self.filePath))
            self.file_put_contents(self.filePath,pickle.dumps(values))

            if n>99999:
                self.writecnt +=1
                if self.writecnt>10:
                    #only write every 10 times to prevent from wearing the flash
                    self.file_put_contents(self.filePath2,pickle.dumps(values))
                    print("Writing to persistent storage: "+self.filePath2)
                    self.writecnt = 0

            if n>10000:
                print("Persitent storage file is too big ({}) -- reseting to new file ".format(n))
                self.faildate = 0
        else:
            print("Data posting ok!")
            self.faildate = 0
            if os.path.isfile(self.filePath2):
                print("Deleting persistent storage file "+self.filePath2)
                os.remove(self.filePath2)
            self.uploadQueue()

    def uploadQueue(self):
            path = os.path.dirname(os.path.abspath(__file__))
            for file in glob.glob(path+'/*.pickle'):
                    print "Uploading {0}: ".format(file),
                    values = pickle.loads(self.file_get_contents(file))
                    if self.httpPost(values):
                            print("ok!")
                            os.remove(file)
                    else:
                            print("nope...")
            print("upload queue("+path+"): all done.")



stop_worker = 0

def loop(usbport):
    print("Starting reading sensor "+SENSORID+" on port "+usbport)
    reader = SDS011Reader(usbport)
    uploader = SensorDataUploader(SENSORID)
    t = threading.Thread(target=worker, args=(reader,))
    t.start()
    t1 = threading.Thread(target=buzzer, args=(reader, pin))
    t1.start()
    while 1:
        try:
            time.sleep(INTERVAL_UPLOAD)
            uploader.postValues(reader.getClear())
        except:
            print "Bye"
            GPIO.cleanup()

            reader.stop()
            if(uploader.filePath):
                os.remove(uploader.filePath)
            if(uploader.filePath2):
                os.remove(uploader.filePath2)

            sys.exit()
    
def worker(reader):
    while reader.started():
        reader.read()

def buzzer(reader, pin):
    while reader.started():
        if reader.needsAlarm():
            pin.start(25)
            for dc in range(25, 101, 5):
                pin.ChangeDutyCycle(dc)
                pin.ChangeFrequency(dc*reader.needsAlarm())
                time.sleep(0.02)
            for dc in range(100, 26, -5):
                pin.ChangeDutyCycle(dc)
                pin.ChangeFrequency(dc*reader.needsAlarm())
                time.sleep(0.04)
            pin.stop()
            reader.stopAlarm()
        time.sleep(.1)

if len(sys.argv)==2:
    loop(sys.argv[1])
else:
    loop(USBPORT)

#p.start(0)
#try:
#    while 1:
#        for dc in range(25, 101, 5):
#            p.ChangeDutyCycle(dc)
#            p.ChangeFrequency(dc*5)
#            time.sleep(0.05)
#        for dc in range(100, 26, -5):
##            p.ChangeDutyCycle(dc)
#            p.ChangeFrequency(dc*5)
#            time.sleep(0.05)
#except KeyboardInterrupt:
#    pass
#p.stop()

