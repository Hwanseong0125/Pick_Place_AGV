from IPython.display import display
import ipywidgets
import ipywidgets.widgets as widgets
import traitlets
from jetbot import Robot, Camera, bgr8_to_jpeg
from SCSCtrl import TTLServo
import base64
import threading
import paho.mqtt.client as mqtt
from datetime import datetime
import pytz
import time
import json
import random as rd
import torchvision
import torch
import torchvision.transforms as transforms
import torch.nn.functional as F
import cv2
import PIL.Image
import numpy as np

# --- 카메라, 객체 초기화 ---
robot = Robot()
camera = Camera()

# --- MQTT 선언 ---
# 한국 시간대 (Asia/Seoul)로 설정
korea_timezone = pytz.timezone("Asia/Seoul")

#Broker IP Address 와 Port
#라즈베리파이5의 IP 주소로 수정필요
address = "add"
port = 1883

commandTopic = "AGV/command"
sensingTopic = "AGV/sensing"
sensingData = {
    "time" : "None",
    "num1": 0.15,
    "num2": 0.99,
    "is_finish": 0,
    "manual_mode" : "off"
}

publishingData = None

# --- Road Following + Working Area Recognition ---

# 0. 전역 변수 선언
areaA_color = None
areaB_color = None
areaA = None
areaB = None
findArea = None

running_flag = False

# 1. model 불러오기 (변경 없음)
model = torchvision.models.resnet18(pretrained=False)
model.fc = torch.nn.Linear(512, 2)
model.load_state_dict(torch.load('model'))

device = torch.device('cuda')
model = model.to(device)
model = model.eval().half()

mean = torch.Tensor([0.485, 0.456, 0.406]).cuda().half()
std = torch.Tensor([0.229, 0.224, 0.225]).cuda().half()
print('model load success')

# 2. frame 관련 설정
frame_width = 224
frame_height = 224
camera_center_X = int(frame_width / 2)
camera_center_Y = int(frame_height / 2)

# 3. 색상 정의
colors = [
    {'name': 'red',    'lower': np.array([3, 139, 181]),  'upper': np.array([80, 145, 220])},
    {'name': 'green',  'lower': np.array([40, 50, 50]),   'upper': np.array([90, 255, 255])},
    {'name': 'blue',   'lower': np.array([100, 130, 0]),  'upper': np.array([135, 255, 255])},
    {'name': 'purple', 'lower': np.array([140, 50, 50]),  'upper': np.array([155, 255, 255])},
    {'name': 'yellow', 'lower': np.array([26, 160, 180]), 'upper': np.array([30, 230, 220])},
    {'name': 'orange', 'lower': np.array([10, 100, 100]), 'upper': np.array([25, 255, 255])}
]

# 4. WorkingAreaFind 클래스 (위젯 제거 및 인자 추가)
class WorkingAreaFind(threading.Thread):
    def __init__(self, areaA_color, areaB_color):
        super().__init__()
        self.th_flag = True
        self.imageInput = 0
        self.flag = 1
        self.areaA_color = areaA_color
        self.areaB_color = areaB_color

    def run(self):
        global findArea
        while self.th_flag:
            self.imageInput = camera.value
            hsv = cv2.cvtColor(self.imageInput, cv2.COLOR_BGR2HSV)
            hsv = cv2.blur(hsv, (15, 15))
            
            areaA_mask = cv2.inRange(hsv, self.areaA_color['lower'], self.areaA_color['upper'])
            areaA_mask = cv2.erode(areaA_mask, None, iterations=2)
            areaA_mask = cv2.dilate(areaA_mask, None, iterations=2)
            
            areaB_mask = cv2.inRange(hsv, self.areaB_color['lower'], self.areaB_color['upper'])
            areaB_mask = cv2.erode(areaB_mask, None, iterations=2)
            areaB_mask = cv2.dilate(areaB_mask, None, iterations=2)

            AContours, _ = cv2.findContours(areaA_mask.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            BContours, _ = cv2.findContours(areaB_mask.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if AContours and self.flag == 1:
                self.findCenter(areaA, AContours)
            elif BContours and self.flag == 2:
                self.findCenter(areaB, BContours)
            time.sleep(0.1)

    def findCenter(self, name, Contours):
        global findArea
        c = max(Contours, key=cv2.contourArea)
        ((box_x, box_y), radius) = cv2.minEnclosingCircle(c)

        X = int(box_x)
        Y = int(box_y)

        if abs(camera_center_Y - Y) < 20 and abs(camera_center_X - X) < 20:
            if name == areaA and self.flag == 1:
                self.flag = 2
                findArea = areaB
                print(f"{areaA} 도착! 다음 목표: {findArea}")
            elif name == areaB and self.flag == 2:
                self.flag = 1
                findArea = areaA
                print(f"{areaB} 도착! 다음 목표: {findArea}")

    def stop(self):
        self.th_flag = False
        robot.stop()

# 5. RobotMoving
class RobotMoving(threading.Thread):
    def __init__(self):
        super().__init__()
        self.th_flag = True
    
        self.angle = 0.0
        self.angle_last = 0.0
        
    def run(self):
        while self.th_flag:
            image = camera.value
            xy = model(self.preprocess(image)).detach().float().cpu().numpy().flatten()
            x = xy[0]
            y = (0.5 - xy[1]) / 2.0
            
            #x_slider.value = x
            #y_slider.value = y
            
            #인공지능 무인운반로봇(AGV)의 속도 표시
            speed_slider_value = 0.4 # 속도 고정
            
            #image_widget.value = bgr8_to_jpeg(image)
            
            #조향값 계산
            self.angle = np.arctan2(x, y)
            
            if not self.th_flag:
                break
            #PID 제어를 이용한 모터 제어
            pid = self.angle * 0.2 # steering 고정
            self.angle_last = self.angle

            #슬라이더에 표시
            steering_slider_value = pid

            robot.left_motor.value = max(min(speed_slider_value + steering_slider_value, 1.0), 0.0)
            robot.right_motor.value = max(min(speed_slider_value - steering_slider_value, 1.0), 0.0)
            time.sleep(0.1)
        robot.stop()
    
    def preprocess(self, image):
        image = PIL.Image.fromarray(image)
        image = transforms.functional.to_tensor(image).to(device).half()
        image.sub_(mean[:, None, None]).div_(std[:, None, None])
        return image[None, ...]

    def stop(self):
        self.th_flag = False
        robot.stop()


# --- AGV 제어 함수 ---
def agv_stop():
    robot.stop()
    
def agv_forward():
    robot.forward(0.4)

def agv_backward():
    robot.backward(0.4)

def agv_left():
    robot.left(0.3)

def agv_right():
    robot.right(0.3)
    

def agv_areatoarea(arg1, arg2):
    global areaA, areaB, areaA_color, areaB_color, findArea
    global goalFinding, roadFinding, running_flag, camera_link

    if not running_flag:
        areaA = arg1
        areaB = arg2
        areaA_color = next((color for color in colors if color['name'] == areaA), None)
        areaB_color = next((color for color in colors if color['name'] == areaB), None)
        findArea = areaA

        if areaA_color is None or areaB_color is None:
            print("유효하지 않은 색상 이름입니다.")
            return

        # 카메라 링크 끊기
        camera_link.unlink()

        goalFinding = WorkingAreaFind(areaA_color, areaB_color)
        goalFinding.start()

        roadFinding = RobotMoving()
        roadFinding.start()

        running_flag = True
        print("AGV 시작")
    else:
        roadFinding.stop()
        goalFinding.stop()
        camera_link = traitlets.dlink((camera, 'value'), (image_widget, 'value'), transform=bgr8_to_jpeg)
        running_flag = False
        print("AGV 중지됨")
    
# --- MQTT protocol ---
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(commandTopic, 1)
        print("connected OK")
    else:
        print("Bad connection Returned code=", rc)

def on_publish(client, userdata, result):
    print("data published")

def on_message(client, userdata, msg):
    global message
    message = json.loads(msg.payload.decode("utf-8"))
    #print(message, type(message))
    
    if message["cmd_string"] == "go":      agv_forward()
    elif message["cmd_string"] == "mid":   agv_stop()
    elif message["cmd_string"] == "left":  agv_left()
    elif message["cmd_string"] == "right": agv_right()
    elif message["cmd_string"] == "back":  agv_backward()
    elif message["cmd_string"] == "stop":  print('stop')
    elif message["cmd_string"] == "Area":  agv_areatoarea(message["arg_string1"],message["arg_string2"])
    elif message["cmd_string"] == "exit":
        print('exit')
        publishingData.stop()
        agv_stop()
        
class sensorReadPublish(threading.Thread):
    
    def __init__(self):
        super().__init__()
        self.th_flag = True
        
    def run(self):

        while self.th_flag:
            frame = camera.value

            # 이미지를 JPEG로 인코딩
            jpeg_frame = bgr8_to_jpeg(frame)

            # JPEG 이미지를 base64로 인코딩
            jpg_as_text = base64.b64encode(jpeg_frame).decode('utf-8')

            # MQTT로 이미지 전송
            client.publish("robot/camera", jpg_as_text)

            time.sleep(0.1)  # 10fps로 이미지 전송
            
        
    def stop(self):
        self.th_flag = False
        
        
def main():
    
    #MQTT Client 객체 생성
    client = mqtt.Client()
    #Broker 연결
    client.connect(address, port)
    
    #Callback 함수 바인딩
    client.on_connect = on_connect
    client.on_publish = on_publish
    client.on_message = on_message
    
    # publish
    publishingData = sensorReadPublish()
    publishingData.start()
    
    #loop_start() 를 이용해 비동기적으로 MQTT Protocol 동작
    client.loop_start()
        
    
    
if __name__ == "__main__":
    main()
        
        
        
        
        
        
        
        
        
        
        
        
        
        