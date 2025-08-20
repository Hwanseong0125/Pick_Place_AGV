# This Python file uses the following encoding: utf-8
import sys
import paho.mqtt.client as mqtt
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import pytz
import base64
import numpy as np
import cv2
import openai
import json
import re
import uuid

from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage
from ui_form import Ui_MainWindow

#MQTT command Topic
commandTopic = "robot/command"

#MQTT sensor Topic
sensingTopic = "robot/sensing"

#Firebase 초기화
cred = credentials.Certificate('pjt_key.json')
firebase_admin.initialize_app(cred)
db = firestore.client()
korea_timezone = pytz.timezone("Asia/Seoul")

openai.api_key = ""

def write_log_to_firestore(cmd_string, arg_string1 = "default_none", arg_string2 = "default_none"):
    current_time = datetime.now(korea_timezone)
    time_doc = current_time.strftime("%Y-%m-%d %H:%M:%S" + f" {uuid.uuid4().hex[:6]}")
    commandData = {
        "cmd_string": cmd_string,
        "arg_string1": arg_string1,
        "arg_string2": arg_string2
    }
    db.collection("commandTable").document(time_doc).set(commandData)
    print(f"[Firestore] Command logged: {cmd_string} at {time_doc}")

class MainWindow(QMainWindow):

    #MQTT로 들어온 data를 받아줄 list 생성
    sensorData = list()
    #sensorData 중 최신 15개 data만 저장할 list
    sensingDataList = list()

    #MQTT로 보낼 command dict
    commandData = dict()
    #commandData 전체
    commandDataList = list()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.mqtt_client = None
        self.auto_mode = False  # 오토 모드 상태 초기화
        self.mqtt_mode = False
        self.init()

    def init(self):
        print('init')

        self.ui.startButton.clicked.connect(self.start)
        self.ui.stopButton.clicked.connect(self.stop)

        self.ui.midButton.clicked.connect(self.mid)
        #self.ui.goButton.clicked.connect(self.go)
        #self.ui.backButton.clicked.connect(self.back)
        #self.ui.leftButton.clicked.connect(self.left)
        #self.ui.rightButton.clicked.connect(self.right)
        self.ui.goButton.pressed.connect(self.go)
        self.ui.backButton.pressed.connect(self.back)
        self.ui.leftButton.pressed.connect(self.left)
        self.ui.rightButton.pressed.connect(self.right)
        #self.ui.goButton.released.connect(self.mid)
        self.ui.backButton.released.connect(self.mid)
        self.ui.leftButton.released.connect(self.mid)
        self.ui.rightButton.released.connect(self.mid)

        #target 버튼
        self.ui.targetStartButton.clicked.connect(self.target_start)
        self.ui.targetStopButton.clicked.connect(self.target_stop) #target_stop으로 변경

        #openai 버튼
        self.ui.entButton.clicked.connect(self.enter)

        # Auto 버튼 클릭 시, 오토 모드 토글
        #self.ui.autoButton.clicked.connect(self.toggle_auto_mode)

        #self.register_slider_with_reset(self.ui.camSlider_0, self.ui.camReset_0, "cam_0")
        #self.register_slider_with_reset(self.ui.camSlider_1, self.ui.camRest_1, "cam_1", -30)
        #self.register_slider_with_reset(self.ui.armSlider_0, self.ui.armReset_0, "arm_0")
        #self.register_slider_with_reset(self.ui.armSlider_1, self.ui.armReset_1, "arm_1")
        #self.register_slider_with_reset(self.ui.handSlider, self.ui.handReset, "hand")

        # 초기 상태 설정
        self.update_status()

    def makeCommandData(self, str, arg, finish):
        current_time = datetime.now(korea_timezone)
        self.commandData["time"] = current_time.strftime("%Y-%m-%d %H:%M:%S")
        self.commandData["cmd_string"] = str
        self.commandData["arg_string"] = arg
        self.commandData["is_finish"] = finish
        return self.commandData

    #슬라이더와 리셋 버튼 등록, init_value로 초기값 등록
    def register_slider_with_reset(self, slider, reset_button, label, init_value = 0):
        def on_reset():
            self.commandData = self.makeCommandData(label, init_value, 0)
            slider.setValue(init_value)
            self.append_log(f"[{label}] reset to {str(init_value)}")
            self.publish(commandTopic, json.dumps(self.commandData))
            write_log_to_firestore(label, f"MQTT status: {self.mqtt_mode}", init_value)
            self.commandData = dict()

        def on_slider_released():
            self.commandData = self.makeCommandData(label, slider.value(), 0)
            value = slider.value()
            self.append_log(f"[{label}] slider released: {value}")
            self.publish(commandTopic, json.dumps(self.commandData))
            write_log_to_firestore(label, f"MQTT status: {self.mqtt_mode}", value)
            self.commandData = dict()

        reset_button.clicked.connect(on_reset)
        slider.setValue(init_value)
        slider.sliderReleased.connect(on_slider_released)

    def append_log(self, msg: str):
        current_time = datetime.now(korea_timezone).strftime('%H:%M:%S')
        formatted_msg = f"[{current_time}] {msg}"
        self.ui.logText.appendPlainText(formatted_msg)

    def on_connect(self, client, userdata, flags, rc):
        """
        MQTT 클라이언트가 서버와 연결되었을 때 호출되는 콜백 함수
        :param client: 연결된 MQTT 클라이언트 객체
        :param userdata: 사용자 정의 데이터 (옵션)
        :param flags: 연결 플래그
        :param rc: 연결 상태 코드 (0이 성공, 그 외 값은 오류 코드)
        """
        if rc == 0:
            self.append_log("MQTT 연결 성공")
        else:
            self.append_log(f"MQTT 연결 실패: 오류 코드 {rc}")

        # 연결 상태 업데이트
        self.update_mqtt_status()

    def setup_mqtt(self):
        if self.mqtt_client:  # 이미 클라이언트가 존재하면 먼저 종료
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

        self.mqtt_client = mqtt.Client(protocol=mqtt.MQTTv311)
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.connect("127.0.0.1", 1883, 60)
        self.mqtt_client.subscribe("robot/camera")  # 영상 수신 토픽 구독
        self.mqtt_client.loop_start()
        self.append_log("MQTT 연결됨")
        print("MQTT 연결됨")

    def disconnect_mqtt(self):
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self.append_log("MQTT 연결 종료")
            print("MQTT 연결 종료")
            self.mqtt_client = None
            self.update_mqtt_status()

    def publish(self, topic, message):
        if self.mqtt_client:
            self.mqtt_client.publish(topic, message)
            self.append_log(f"MQTT Publish: topic='{topic}', message='{message}'")
            print(f"MQTT Publish: topic='{topic}', message='{message}'")
        else:
            self.append_log("MQTT 연결이 안 되어 있어 publish 생략됨")
            print("MQTT 연결 없음 - publish 생략")

    def on_mqtt_message(self, client, userdata, msg):
        if msg.topic == "robot/camera":
            try:
                img_bytes = base64.b64decode(msg.payload)
                nparr = np.frombuffer(img_bytes, np.uint8)
                img_np = cv2.imdecode(nparr, cv2.IMREAD_COLOR)  # BGR 이미지
                img_rgb = cv2.cvtColor(img_np, cv2.COLOR_BGR2RGB)

                h, w, ch = img_rgb.shape
                bytes_per_line = ch * w
                q_img = QImage(img_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(q_img)
                self.ui.videoLabel.setPixmap(pixmap)
            except Exception as e:
                print("Image decode error:", e)

    def toggle_auto_mode(self):
        # 오토 모드를 토글하고, 상태에 맞는 토픽을 MQTT로 전송
        self.auto_mode = not self.auto_mode
        if self.auto_mode:
            self.append_log("Auto mode: ON")
            self.publish("robot/command", "auto_on")
            self.ui.autoStatus.setText("ON")
        else:
            self.append_log("Auto mode: OFF")
            self.publish("robot/command", "auto_off")
            self.ui.autoStatus.setText("OFF")

    #MQTT 상태 갱신
    def update_mqtt_status(self):
        if self.mqtt_client and self.mqtt_client.is_connected():
            self.ui.mqttStatus.setText("ON")
            self.mqtt_mode = True
        else:
            self.ui.mqttStatus.setText("OFF")
            self.mqtt_mode = False

    def update_status(self):
        # 초기 상태를 OFF로 설정
        self.ui.autoStatus.setText("OFF")
        self.ui.mqttStatus.setText("OFF")

    #아래 이벤트 함수들은 호출 시점에 mqtt가 연결되어 유효한 호출이었는지 firebase에 arg로 전달
    def start(self):
        print('start')
        write_log_to_firestore("start", f"MQTT status: {self.mqtt_mode}")
        self.setup_mqtt()
        self.commandData = self.makeCommandData("start", 100, 1)
        self.append_log("Command sent: start")
        self.publish(commandTopic, json.dumps(self.commandData))
        self.commandData = dict()

    def stop(self):
        self.commandData = self.makeCommandData("stop", 100, 1)
        self.publish(commandTopic, json.dumps(self.commandData))
        self.commandDataList.append(self.commandData)
        self.commandData = dict()

        self.client.loop_stop()
        print(self.commandDataList)
        write_log_to_firestore("stop", f"MQTT status: {self.mqtt_mode}")

        # print('stop')
        # self.append_log("Command sent: stop")
        # write_log_to_firestore("stop", f"MQTT status: {self.mqtt_mode}")
        # self.publish("robot/command", "stop")
        # self.disconnect_mqtt()

    def go(self):
        self.commandData = self.makeCommandData("go", 100, 1)
        self.publish(commandTopic, json.dumps(self.commandData))

        self.commandDataList.append(self.commandData)
        self.commandData = dict()
        print(self.commandDataList)
        write_log_to_firestore("go", f"MQTT status: {self.mqtt_mode}")

        # print('go')
        # self.append_log("Command sent: go")
        # write_log_to_firestore("go", f"MQTT status: {self.mqtt_mode}")
        # self.publish("robot/command", "go")

    def mid(self):
        self.commandData = self.makeCommandData("mid", 100, 1)
        self.publish(commandTopic, json.dumps(self.commandData))

        self.commandDataList.append(self.commandData)
        self.commandData = dict()
        print(self.commandDataList)
        write_log_to_firestore("mid", f"MQTT status: {self.mqtt_mode}")


        # print('mid')
        # self.append_log("Command sent: mid")
        # write_log_to_firestore("mid", f"MQTT status: {self.mqtt_mode}")
        # self.publish("robot/command", "mid")

    def back(self):
        self.commandData = self.makeCommandData("back", 100, 1)
        self.publish(commandTopic, json.dumps(self.commandData))

        self.commandDataList.append(self.commandData)
        self.commandData = dict()
        print(self.commandDataList)
        write_log_to_firestore("back", f"MQTT status: {self.mqtt_mode}")

        # print('back')
        # self.append_log("Command sent: back")
        # write_log_to_firestore("back", f"MQTT status: {self.mqtt_mode}")
        # self.publish("robot/command", "back")

    def left(self):
        self.commandData = self.makeCommandData("left", 100, 1)
        self.publish(commandTopic, json.dumps(self.commandData))

        self.commandDataList.append(self.commandData)
        self.commandData = dict()
        print(self.commandDataList)
        write_log_to_firestore("left", f"MQTT status: {self.mqtt_mode}")

        # print('left')
        # self.append_log("Command sent: left")
        # write_log_to_firestore("left", f"MQTT status: {self.mqtt_mode}")
        # self.publish("robot/command", "left")

    def right(self):
        self.commandData = self.makeCommandData("right", 100, 1)
        self.publish(commandTopic, json.dumps(self.commandData))

        self.commandDataList.append(self.commandData)
        self.commandData = dict()
        print(self.commandDataList)
        write_log_to_firestore("right", f"MQTT status: {self.mqtt_mode}")

        # print('right')
        # self.append_log("Command sent: right")
        # write_log_to_firestore("right", f"MQTT status: {self.mqtt_mode}")
        # self.publish("robot/command", "right")

    def match_target(self, val):
        target_list = {"Red": 1, "Yellow": 2, "Orange": 3, "Green": 4, "Purple": 5, "Blue": 6}
        ret = target_list[val]
        return ret

    def target_start(self):
        #start = self.match_target(self.ui.startCombo.currentText())
        #dest = self.match_target(self.ui.destCombo.currentText())
        start = self.ui.startCombo.currentIndex() + 1
        dest = self.ui.destCombo.currentIndex() + 1

        self.commandData = self.makeCommandData("target", start, dest)
        self.publish(commandTopic, json.dumps(self.commandData))
        self.commandData = dict()

        write_log_to_firestore("target", f"MQTT status: {self.mqtt_mode}", self.ui.startCombo.currentText() + " " + self.ui.destCombo.currentText())

    def target_stop(self):
        self.commandData = self.makeCommandData("target_stop", 100, 1)
        self.publish(commandTopic, json.dumps(self.commandData))
        self.commandData = dict()

        write_log_to_firestore("target_stop", f"MQTT status: {self.mqtt_mode}", self.ui.startCombo.currentText() + " " + self.ui.destCombo.currentText())

    def enter(self):
        prompt_input = self.ui.promptText.toPlainText()
        self.append_log(f"Prompt sent: {prompt_input}")

        current_floor = getattr(self, "current_floor", None)
        context = f"The current floor is {current_floor}." if current_floor is not None else ""

        final_prompt = f"""
        You are an assistant that translates natural language commands for an AGV (automated guided vehicle) into a specific control format.
        Your task is to extract the starting and destination locations from the user's instruction. Locations are represented as numbers from 1 to 6.

        The output must begin with the location pair in this exact format: {{start_number, dest_number}}.
        This pair should appear at the very beginning of the response, followed by an optional short explanation if needed.

        Sometimes users will:
        - Specify floor numbers directly (e.g., '1st floor to 4th floor')
        - Use place names with mappings (e.g., 'hospital is 1, police is 3')
        - Use relative terms (e.g., 'one floor up' or 'same floor')

        Examples:
        - "Move from the 1st to the 4th floor." → {{1, 4}}: Moving from 1st to 4th floor.
        - "Hospital to police station. Hospital is location 1, police is 3." → {{1, 3}}: Transferring from hospital to police.
        - "I'm currently on 3rd floor. Move one floor up." → {{3, 4}}: Ascending from floor 3 to 4.

        Output must start with the pair like this: {{start, dest}}: your optional message here

        {context}
        User command: "{prompt_input}"
        Output:
        """.strip()

        try:
            response = openai.chat.completions.create(
                model="gpt-4o-mini",  # 또는 gpt-4o-mini, gpt-3.5-turbo 등
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": final_prompt}
                ],
                max_tokens=50,
                temperature=0.3,
            )
            result_text = response.choices[0].message.content.strip()

            # 예시 MQTT publish 가능 지점
            # self.publish("robot/command", result_text)

        except Exception as e:
            result_text = f"Error: {str(e)}"

        write_log_to_firestore("openAI request", str(prompt_input), str(result_text))
        self.ui.promptText_2.setPlainText(result_text)
        self.ui.promptText.clear()

        match = re.match(r"\{(\d+),\s*(\d+)\}", result_text.strip().lower())
        if match:
            var1 = int(match.group(1))
            var2 = int(match.group(2))
            self.commandData = self.makeCommandData("target", var1, var2)
            self.publish(commandTopic, json.dumps(self.commandData))
            write_log_to_firestore("prompt", f"MQTT status: {self.mqtt_mode}", str(var1) + " " + str(var2))
            self.commandData = dict()
        else:
            print("매칭되는 형식이 없습니다.")

    def closeEvent(self, event):
        print("프로그램 종료")
        self.disconnect_mqtt()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = MainWindow()
    widget.show()
    sys.exit(app.exec())
