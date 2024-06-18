import time
import paho.mqtt.client as mqtt


def on_publish(client, userdata, mid, reason_code, properties):
    # reason_code and properties will only be present in MQTTv5. It's always unset in MQTTv3
    try:
        userdata.remove(mid)
    except KeyError:
        print("on_publish() is called with a mid not present in unacked_publish")
        print("This is due to an unavoidable race-condition:")
        print("* publish() return the mid of the message sent.")
        print("* mid from publish() is added to unacked_publish by the main thread")
        print("* on_publish() is called by the loop_start thread")
        print("While unlikely (because on_publish() will be called after a network round-trip),")
        print(" this is a race-condition that COULD happen")
        print("")
        print("The best solution to avoid race-condition is using the msg_info from publish()")
        print("We could also try using a list of acknowledged mid rather than removing from pending list,")
        print("but remember that mid could be re-used !")


def publish(message, topic, chanel=0):
    if topic == "emotion":
        dirct = "emotion"
    elif topic == "head":
        dirct = f"/angles/{topic}/{chanel}"
    elif topic == "left_arm":
        dirct = f"telmeow/angles/{topic}/{chanel}"
    elif topic == "right_arm":
        dirct = f"telmeow/angles/{topic}/{chanel}"
    else:
        dirct = ""
        message = "There's an error"

    msg_inf = mqttc.publish(f"telmeow/{dirct}", f"{message}", qos=1)
    unacked_publish.add(msg_inf.mid)
    msg_inf.wait_for_publish()

unacked_publish = set()
mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqttc.on_publish = on_publish

mqttc.user_data_set(unacked_publish)
mqttc.connect("mqtt.eclipseprojects.io")

mqttc.loop_start()

# Our application produce some messages
publish("sad", "emotion")
publish("10", "head", 1)
publish("15", "head", 2)
publish("30", "left_arm", 1)
publish("0", "left_arm", 2)
publish("-20", "right_arm", 1)
publish("20", "right_arm", 2)

# Wait for all message to be published
while len(unacked_publish):
    time.sleep(0.1)

mqttc.disconnect()
mqttc.loop_stop()
