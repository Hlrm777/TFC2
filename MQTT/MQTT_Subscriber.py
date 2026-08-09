import paho.mqtt.client as mqtt


def on_subscribe(client, userdata, flags, reason_code, properties):
    print(f"Connected with result code: '{reason_code}'")
    print("Subscribe to the topic: 'telmeow/#'")
    print("*"*30)

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("telmeow/#")


def on_message(client, userdata, msg):
    print(msg.topic+": "+str(msg.payload).replace("b","").replace("'",""))
    print("-"*25)

mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqttc.on_connect = on_subscribe
mqttc.on_message = on_message

mqttc.connect("mqtt.eclipseprojects.io", 1883, 60)
# Blocking call that processes network traffic, dispatches callbacks and handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a manual interface.
mqttc.loop_forever()
