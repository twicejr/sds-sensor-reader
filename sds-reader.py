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


SENSORID = "schuurstof"
USBPORT  = "/dev/ttyS0"
POSTURL  = "/weer/storedust"
POSTHOST = "www.kanbeter.info"

class SDS011Reader:

    def __init__(self, inport):
        self.serial = serial.Serial(port=inport,baudrate=9600)

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



    def read( self, duration ):
        start = os.times()[4]

        count = 0
        species = []
        speciesType = ["pm2.5-mg","pm10-mg"]

        while os.times()[4]<start+duration:
            try:
                values = self.readValue()
                dt = datetime.now().isoformat()
                species.append([dt, values])
                count += 1
                #dt = os.times()[4]-start
                print("[{:18}] PM2.5:{:4.1f} PM10:{:4.1f}".format(
                    dt,float(values[0])/10,float(values[1])/10
                    ))
                time.sleep(1)
            except KeyboardInterrupt:
                print "Bye"
                sys.exit()
            except:
                e = sys.exc_info()[0]
                e = traceback.format_exc(9);
                print("Can not read the sensor data: "+str(e))

        return species


class SensorDataUploader:

    def __init__(self, id):
        self.faildate = 0
        self.writecnt = 0
        self.id = id

    def httpPost(self,idata):
        try:
            postdata = dict(data=dict( 
                id=self.id, 
                data = idata
                ))
            postdata = urllib.urlencode(postdata)
            headers = {"Content-type": "application/x-www-form-urlencoded", "Accept": "text/plain"}
            conn = httplib.HTTPSConnection(POSTHOST)
            conn.request("POST", POSTURL, postdata, headers)
            response = conn.getresponse()
            data = response.read()
            print("Posting {2} bytes -> {0} {1} ".format(response.status, response.reason, len(postdata)))
            conn.close()
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
        filePath = os.path.join(tempfile.gettempdir(), self.id+".pickle")
        if self.faildate==0:
            self.faildate = time.strftime("%Y-%m-%d-%H-%M-%S")
        filePath2 = os.path.join(os.path.dirname(__file__), "pending."+self.id+"."+self.faildate+".pickle")

        if os.path.isfile(filePath) and os.path.getsize(filePath)>0:
            ovalues = pickle.loads(self.file_get_contents(filePath))
            print(ovalues)

            print("previous queue size has "+str(len(ovalues))+" entries")
            values = ovalues + values
            os.remove(filePath)
            print(values)

        if not self.httpPost(values):
            n = len(values)
            print("upload not ok... there are now {0} entries pending ({1}).".format(n,filePath))
            self.file_put_contents(filePath,pickle.dumps(values))

            if n>150:
                self.writecnt +=1
                if self.writecnt>10:
                    #only write every 10 times to prevent from wearing the flash
                    self.file_put_contents(filePath2,pickle.dumps(values))
                    print("Writing to persistent storage: "+filePath2)
                    self.writecnt = 0

            if n>10000:
                print("Persitent storage file is too big ({}) -- reseting to new file ".format(n))
                self.faildate = 0
        else:
            print("Data posting ok!")
            self.faildate = 0
            if os.path.isfile(filePath2):
                print("Deleting persistent storage file "+filePath2)
                os.remove(filePath2)
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




def loop(usbport):
    print("Starting reading sensor "+SENSORID+" on port "+usbport)
    reader = SDS011Reader(usbport) 
    uploader = SensorDataUploader(SENSORID) 
    while 1:
        uploader.postValues(reader.read(15))


if len(sys.argv)==2:
    loop(sys.argv[1])
else:
    loop(USBPORT)


