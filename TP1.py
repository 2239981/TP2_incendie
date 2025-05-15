# === Imports ===
import tkinter as tk
from tkinter import ttk
import RPi.GPIO as GPIO
from gpiozero import LED, Buzzer, Servo
import adafruit_dht
import time
import board
import mysql.connector
from mysql.connector import Error
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime
import requests
import json
import threading
import paho.mqtt.client as mqtt

# === Variables globales ===
global client
client = None

THINGSBOARD_TOKEN = "5hfL5KZHLKkbDdolo1Vr" 
THINGSBOARD_URL = f"http://thingsboard.cloud/api/v1/{THINGSBOARD_TOKEN}"
HEADERS = {'Content-Type': 'application/json'}

GPIO.setmode(GPIO.BCM)
DHTPin = 17
PortePin = 18
AlarmePin = 23
LED_Pin = 24

# === Initialisations ===
led = LED(LED_Pin)
buzzer = Buzzer(AlarmePin)
servo = Servo(PortePin)
dht_device = adafruit_dht.DHT11(board.D17)

cred = credentials.Certificate("/home/thoma/Downloads/firebase-key.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://projet2-a895e-default-rtdb.firebaseio.com/'
})

mode_test_actif = False
alarme_active = False
temperature_manuelle = None
porte_manuellement = False
etat_servo_actuel = None

derniere_temperature_capteur = None
derniere_hum_capteur = None

# ========== Synchronisation avec ThingsBoard ==========
def get_test_mode_from_thingsboard():
    global mode_test_actif
    try:
        response = requests.get(f"{THINGSBOARD_URL}/attributes", headers=HEADERS)
        if response.status_code == 200:
            data = response.json()
            mode = data.get("shared", {}).get("monNouveauMode", False) 
            mode_test_actif = mode
    except Exception as e:
        print(f"Error getting testMode from ThingsBoard: {e}")

def get_mode_test():
    global mode_test_actif
    return mode_test_actif

def set_mode_test(nouvel_etat):# Aider par internet
    global mode_test_actif
    if isinstance(nouvel_etat, bool):
        mode_test_actif = nouvel_etat
        mettre_a_jour_interface_mode_test()
        if not nouvel_etat:
            temperature_manuelle = None
            mettre_a_jour_affichage_manuel()
        if client and client.is_connected():
            payload = json.dumps({"monNouveauMode": mode_test_actif})
            result = client.publish("v1/devices/me/attributes/update", payload, qos=0)
        
def mettre_a_jour_interface_mode_test():
    global mode_test_label, mode_test_actif, style
    mode_test_label.config(
        text=f"Mode Test : {'Activé' if mode_test_actif else 'Désactivé'}", fg="blue"
    )
    style.configure("GreenButton.TButton", foreground="black" if mode_test_actif else "grey")
    style.configure("RedButton.TButton", foreground="black" if mode_test_actif else "grey")
    print(f"Interface mise à jour : texte du label = {mode_test_label.cget('text')}")   
    
def mettre_a_jour_affichage_manuel():
    global temperature_manuelle, temp_valeur, humidite_manuelle, humidite_valeur, mode_test_actif
    global derniere_temperature_capteur
    
    if mode_test_actif:
        if temperature_manuelle is not None:
            temp_valeur.config(text=f"{temperature_manuelle}°C", fg="red")
    else:
        if temperature_manuelle is None:
            if derniere_temperature_capteur is not None:
                temp_valeur.config(text=f"{derniere_temperature_capteur}", fg="red")

        else:
             temp_valeur.config(text=f"{derniere_temperature_capteur}", fg="red")

# ========== Envoi vers ThingsBoard ==========
def envoyer_donnees_vers_thingsboard(temperature, humidite):
    payload = {"temperature": temperature, "humidite": humidite}
    try:
        response = requests.post(f"{THINGSBOARD_URL}/telemetry", headers=HEADERS, data=json.dumps(payload))
        print("✅ Données envoyées à ThingsBoard" if response.status_code == 200 else f"❌ Erreur: {response.text}")
    except Exception as e:
        print(f"❌ Exception TB : {e}")

# ========== Lecture du capteur ==========
def lire_temperature_et_humidite():
    for _ in range(3):
        try:
            return dht_device.temperature, dht_device.humidity
        except RuntimeError as e:
            print(f"DHT11 erreur : {e}")
            time.sleep(1)
    return None, None

# ========== Gestion de l’alarme ==========
def alarme(temperature):
    global alarme_active
 
    if temperature >= 25:
        if not alarme_active:
            alarme_active = True
            led.on()
            buzzer.on()
            trappe_label.config(text="Trappe : Ouverte")
            servo.max()
            if client and client.is_connected():# Aider par internet
                client.publish("v1/devices/me/attributes", json.dumps({
                    "ledState": True,
                    "buzzerState": True,
                    "buttonState": True
                }))
                print("✅ Alarme activée")
    else:
        if alarme_active:
            alarme_active = False
            led.off()
            buzzer.off()
            trappe_label.config(text="Trappe : Fermée")
            servo.min()
            if client and client.is_connected(): # Aider par internet
                client.publish("v1/devices/me/attributes", json.dumps({
                    "ledState": False,
                    "buzzerState": False,
                    "buttonState": False
                }))
                print("✅ Alarme désactivée")

# ========== Base de données ==========
def get_db_connection():
    try:
        return mysql.connector.connect(
            host='127.0.0.1', user='root', password='password', database='TP2'
        )
    except Error as e:
        print(f"MySQL erreur : {e}")
        return None

def enregistrer_donnees(temperature, humidite):
    if temperature is not None and humidite is not None:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        connection = get_db_connection()
        if connection:
           try:
              cursor = connection.cursor()
              cursor.execute("INSERT INTO Capteur (temperature, humidite) VALUES (%s, %s)", (temperature, humidite))
              connection.commit()
              print("✅ Données enregistrées dans MySQL")
           except Error as e:
              print(f"MySQL erreur : {e}")
           finally:
              cursor.close()
              connection.close()
        try:
           db.reference('/Capteur').push({
              'temperature': temperature,
              'humidite': humidite,
              'timestamp': now
           })
           print("✅ Données enregistrées dans Firebase")
        except Exception as e:
           print(f"Firebase erreur : {e}")
        envoyer_donnees_vers_thingsboard(temperature, humidite)
    else:
        print("lecteur du capteur impossible")
        temp_valeur.config(text=f"Chargement ... ", fg="red")


# ========== Mise à jour périodique ==========
def update_temp():
    global temperature_manuelle, humidite_manuelle
    global temp_valeur, humidite_valeur
    global derniere_temperature_capteur, derniere_hum_capteur
    temperature = None
    humidite = None
 
    if get_mode_test():
        if temperature_manuelle is None:
            temperature, humidite = lire_temperature_et_humidite()
            if temperature is not None and humidite is not None:
                derniere_temperature_capteur = temperature_manuelle
                derniere_humidite_capteur = humidite
                temp_valeur.config(text=f"{temperature}°C", fg="red")
                alarme(temperature)
                enregistrer_donnees(temperature, humidite)
                print(f"Donnees envoyees : Température: {temperature}°C, Humidité: {humidite}%")
            else:
                temp_valeur.config(text=f"Chargement ... ", fg="red")
 
        else:
            temperature = temperature_manuelle
            _, humidite = lire_temperature_et_humidite()
            if temperature is not None:
                temp_valeur.config(text=f"{temperature}°C", fg="red")
                alarme(temperature)
                print(f"Donnees envoyees (manuel): Température: {temperature}°C")
            else:
                temp_valeur.config(text=f"Erreur température manuelle", fg="orange")
 
    else:
        temperature, humidite = lire_temperature_et_humidite()
        if temperature is not None and humidite is not None:
            derniere_temperature_capteur = temperature
            derniere_hum_capteur = humidite
            temp_valeur.config(text=f"{temperature}°C", fg="red")
            alarme(temperature)
            enregistrer_donnees(temperature, humidite)
            print(f"Donnees envoyees : Température: {temperature}°C, Humidité: {humidite}%")
        else:
            temp_valeur.config(text=f"Chargement ... ", fg="red")
 
    window.after(15000, update_temp)

# ========== Interface ==========
def mode_test_bouton():# Aider par internet
    global temperature_manuelle
    new_mode = not get_mode_test()
    set_mode_test(new_mode)
    if not new_mode:
        temperature_manuelle = None

        

def ajuster_temperature(temp_increment):
    global temperature_manuelle
    if get_mode_test():
        if temperature_manuelle is None:
            temperatureActuel , _ = lire_temperature_et_humidite()
            if temperatureActuel is not None:
                temperature_manuelle = temperatureActuel + temp_increment
            else:
                print(f"Erreur lors de la lecture initial de la temperature")
                return
        else:
            temperature_manuelle += temp_increment
        print(f"Température manuelle ajustée : {temperature_manuelle}")
        mettre_a_jour_affichage_manuel()

def augmenter_temperature():
    ajuster_temperature(1)

def diminuer_temperature():
    ajuster_temperature(-1)

def envoyer_etat_porte_sur_thingsboard(porte_ouverte):
    payload = {"buttonState": "ON" if porte_ouverte else "OFF"}
    try:
        if client and client.is_connected():
            response = client.publish("v1/devices/me/attributes", json.dumps(payload))
    except Exception as e:
        print(f"Erreur envoi porte TB: {e}")

def ouvrir_porte():
    if not get_mode_test():
        return
    servo.max()
    trappe_label.config(text="Trappe : Ouverte")
    envoyer_etat_porte_sur_thingsboard(True)

def fermer_porte():
    if not get_mode_test():
        return
    servo.min()
    trappe_label.config(text="Trappe : Fermée")
    envoyer_etat_porte_sur_thingsboard(False)

def activer_alarme():
    if not get_mode_test():
        return
    global alarme_active
    alarme_active = True
    led.on()
    buzzer.on()
    trappe_label.config(text="Trappe : Ouverte")
    if client and client.is_connected():# Aider par internet
        client.publish("v1/devices/me/attributes", json.dumps({
            "ledState": True,
            "buzzerState": True,
            "buttonState": True
        }))
    else:
        print("MQTT Client non connecté.")
 
def arreter_alarme():
    if not mode_test_actif:
        return
    global alarme_active
    alarme_active = False
    led.off()
    buzzer.off()
    trappe_label.config(text="Trappe : Fermée")
    # Mise à jour dans ThingsBoard via MQTT
    client.publish("v1/devices/me/attributes", json.dumps({"ledState": False, "buzzerState": False,"buttonState": False }))


def mqtt_on_connect(client_instance, userdata, flags, rc):
    global client  
    print(f"Connecte a Thingboard avec le code {rc}")
    if rc == 0:# Aider par internet
        client = client_instance 
        client.subscribe("v1/devices/me/rpc/request/+")
        client.subscribe("v1/devices/me/attributes/response")
        client.subscribe("v1/devices/me/attributes")
    else:
        print(f"Erreur de connexion MQTT : {rc}")
        
def mqtt_on_attribute_update(client, userdata, msg):# Aider par internet
    try:
        data = json.loads(msg.payload)
        if "monNouveauMode" in data:
            global mode_test_actif, temperature_manuelle
            mode_test_actif = data["monNouveauMode"]
            if not mode_test_actif:
                temperature_manuelle = None
                mettre_a_jour_affichage_manuel() 
            else:
                mettre_a_jour_affichage_manuel()     
            mettre_a_jour_interface_mode_test()
    except json.JSONDecodeError as e:
        print(f"MQTT - Erreur de décodage JSON : {e}")
    


def mqtt_on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
        method = data.get("method")
        params = data.get("params")
        request_id = msg.topic.split('/')[-1]

        if method == "setState":
            if params:
                led.on()
                print("MQTT - LED allumée")
            else:
                led.off()
                print("MQTT - LED éteinte")
            
            client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"success": True}))

        elif method == "setBuzzer":
            if params:
                buzzer.on()
                print("MQTT - Buzzer activé")
            else:
                buzzer.off()
                print("MQTT - Buzzer désactivé")
            client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"success": True}))

        elif method == "setLed":
            if params:
                led.on()
                print("MQTT - LED allumée")
            else:
                led.off()
                print("MQTT - LED éteinte")
            client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"success": True}))

        elif method == "augmenteTemperature":
            if mode_test_actif:
                ajuster_temperature(1)
                print("MQTT - Température augmentée")
                client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"success": True}))
            else:
                client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"error": "Mode test inactif"}))

        elif method == "Diminuertemperature":
            if mode_test_actif:
                ajuster_temperature(-1)
                print("MQTT - Température diminuée")
                client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"success": True}))
            else:
                client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"error": "Mode test inactif"}))

            print(f"Mode Test Actif ? {mode_test_actif}, Température manuelle : {temperature_manuelle}")
        elif method == "ouvrirPorte":
           if mode_test_actif:
               ouvrir_porte()
               trappe_label.config(text="Trappe : Ouverte")
               client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"success": True}))
           else:
               client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"error": "Mode test inactif"}))

        elif method == "fermerPorte":
           if mode_test_actif:
               fermer_porte()
               trappe_label.config(text="Trappe : Fermer")
               client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"success": True}))
           else:
               client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"error": "Mode test inactif"}))

        else:
            client.publish(f"v1/devices/me/rpc/response/{request_id}", json.dumps({"error": "Méthode inconnue"}))

    except Exception as e:
        print("MQTT - Erreur RPC:", e)



# MQTT client setup
def lancer_client_mqtt():
    global client
    client = mqtt.Client(protocol=mqtt.MQTTv311)
    client.username_pw_set(THINGSBOARD_TOKEN)
    client.on_connect = mqtt_on_connect
    client.on_message = mqtt_on_message
    client.message_callback_add("v1/devices/me/attributes", mqtt_on_attribute_update)
    client.loop_start()


# Aider par internet
mqtt_thread = threading.Thread(target=lancer_client_mqtt, daemon=True)
mqtt_thread.start()


# ========== Interface Graphique ==========
def main():
    global mode_test_label, temp_valeur, window, style, trappe_label, humidite_valeur

    window = tk.Tk()
    window.title("Poste d'Incendie Intelligent")
    window.geometry("550x500")

    tk.Label(window, text="Système de Surveillance", font=('Arial', 16, 'bold')).pack(pady=20)

    temp_valeur = tk.Label(window, text="Chargement...", font=('Arial', 12, 'bold'), fg="red")
    tk.Label(window, text="Température :", font=('Arial', 13)).pack()
    temp_valeur.pack()

    trappe_label = tk.Label(window, text="Trappe : Fermée", font=("Arial", 12))
    trappe_label.pack(pady=5)


    mode_test_label = tk.Label(window, text="Mode Test : Désactivé", font=('Arial', 13, 'bold'), fg="blue")
    mode_test_label.pack(pady=10)

    ttk.Button(window, text="Basculer Mode Test", command=mode_test_bouton, width=25).pack(pady=10)

    style = ttk.Style()
    style.configure("GreenButton.TButton", background="green", foreground="grey")
    style.configure("RedButton.TButton", background="red", foreground="grey")

    temp_frame = tk.Frame(window)
    temp_frame.pack(pady=10)
    ttk.Button(temp_frame, text="+", width=3, command=augmenter_temperature, style="GreenButton.TButton").pack(side=tk.LEFT, padx=5)
    ttk.Button(temp_frame, text="-", width=3, command=diminuer_temperature, style="RedButton.TButton").pack(side=tk.LEFT)

    trappe_frame = tk.Frame(window)
    trappe_frame.pack(pady=10)
    ttk.Button(trappe_frame, text="Ouvrir", command=ouvrir_porte, style="GreenButton.TButton").pack(side=tk.LEFT, padx=5)
    ttk.Button(trappe_frame, text="Fermer", command=fermer_porte, style="RedButton.TButton").pack(side=tk.LEFT, padx=5)

    alarme_frame = tk.Frame(window)
    alarme_frame.pack(pady=10)
    ttk.Button(alarme_frame, text="Activer", command=activer_alarme, style="GreenButton.TButton").pack(side=tk.LEFT, padx=5)
    ttk.Button(alarme_frame, text="Arrêter", command=arreter_alarme, style="RedButton.TButton").pack(side=tk.LEFT, padx=5)

    get_test_mode_from_thingsboard()
    mettre_a_jour_interface_mode_test()
    mettre_a_jour_affichage_manuel() 

    update_temp()
    window.mainloop()


# ========== Lancement ==========
if __name__ == "__main__":
    main() 
