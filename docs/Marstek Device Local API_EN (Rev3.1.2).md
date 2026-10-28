# **Marstek Device Local API**（Rev 3.1.2）

​		The Local API is provided “as is” for local use only.Use at your own risk. Marstek is not liable for any damages, data loss, or legal issues caused by your use of the API.You are responsible for lawful and appropriate use.

# **Ⅰ**. Preface**：**

**Welcome!**

​		This document provides an introduction to the Local API for Marstek devices, which is  available to device owners and enables integration with third-party systems.

​		While Marstek offers an official mobile app and cloud services, this Local API is designed for advanced users who wish to gain greater control over their devices and seamlessly integrate them into other management platforms.

# **Ⅱ**. General Description

Marstek devices communicate with third-party systems over a Local Area Network (LAN). Before using this API, please ensure that:

1. The Marstek device is properly connected to your home network.

2. The Local API feature has been enabled via the Marstek mobile app

​		Please note that different Marstek models may support only a subset of the commands described in this documentation. Additionally, enabling the Local API may cause certain built-in features of the device to be disabled in order to prevent command conflicts. For detailed information about supported commands and any functional limitations for specific models, please refer to **Chapter ** **4** of this document.

## **2.1 Protocol Format**

​		The protocol utilizes the JSON format, with commands primarily categorized into query commands, configuration commands, and certain special commands.

**Command Format**

| **Property** | **Type**         | **Description**                                              |
| ------------ | ---------------- | ------------------------------------------------------------ |
| id           | number or string | An identifier established by the Client                      |
| method       | string           | A Structured value that holds the parameter values to be used during the invocation of the method. |
| params       | object           | Parameters that the method takes.                            |

**Example**

```
    {
        "id": 0,
        "method": "string",
        "params": {
        "id":0
        }
    }
```

**Device Response Format**

| **Property** | **Type**         | **Description**                    |
| ------------ | ---------------- | ---------------------------------- |
| id           | number or string | Identifier of this request.        |
| src          | string           | Name of the source of the request. |
| result       | object           | Parameters that the method takes.  |

**Error code**

| **Code**         | **MessageMeaning** |                                                              |
| ---------------- | ------------------ | ------------------------------------------------------------ |
| -32700           | Parse error        | Invalid JSON was received by the server.An error occurred on the server while parsing the JSON text |
| -32600           | Invalid Request    | The JSON sent is not a valid Request object                  |
| -32601           | Method not found   | The method does not exist / is not available.                |
| -32602           | Invalid params     | Invalid method parameter(s).                                 |
| -32603           | Internal error     | Internal JSON-RPC error                                      |
| -32000 to -32099 | Server error       | Reserved for implementation-defined server-errors            |

The remainder of the space is available for application defined errors.

**Example response on success**

```
    {
        "id": 0,
        "src": "device",
        "result": {
        "id":0
        }
    }
```

**Example response on error**

```
    {
        "id": 0,
        "src": "Venus-24215ee580e7",
        "error": {
        "code": -32700,
        "message": "Parse error",
        "data": 402
        }
    }
```

## **2.2 API over UDP**

### **2.2.1 First-Time Use**

When users first use the Local API service, they need to follow the configuration process below:

1. Connect the device to power and turn it on;

2. Use the Marstek APP to connect and bind the device, and configure the WiFi network for thedevice or connect it to the Ethernet;

3. Enable the device's API feature in the APP, and set the UDP port number. The default port number is 30000, and the recommended port number is between 49152 and 65535

After completing the above operations, the Marstek device can normally receive UDP commands from the same local area network.

### **2.2.2 Discovering Devices**

To discover Marstek devices within the LAN, a UDP broadcast is utilized. The broadcast content is as follows:

```
    {
        "id": 0,
        "method": "Marstek.GetDevice",
        "params": {
        "ble_mac":"0"
        }
    }
```

​		If there are Marstek devices within the LAN, taking Venus C as an example, the following response will be received

```
    {
        "id": 0,
        "src": "VenusC-123456789012",
        "result": {
        "device":"VenusC",
        "ver":111,
        "ble_mac":"123456789012",
        "wifi_mac":"123456789012",
        "wifi_name":"MY_HOME",
        "ip":"192.168.1.11"
        }
    }
```

*The device's IP address can be directly obtained from the Marstek APP or the home router. If this* functionality is to be used on a long-term basis, it is recommended to configure the device with a static* IP address.*

# **Ⅲ**. Components

This chapter mainly introduces the components and services supported by the Marstek device.

## **3.1 Marstek**

​		Marstek contains some basic information about the product, and is mainly used for discovering devices and querying basic device information.

​		Marstek.GetDevice: Locate Marstek devices on the local area network.

### **3.1.1 Marstek.GetDevice**

**Sending**:

```
    {
        "id": 0,
        "method": "Marstek.GetDevice",
        "params": {
        "ble_mac":"123456789012"
        }
    }
```

**Response**:

```
    {
        "id": 0,
        "src": "VenusC-123456789012",
        "result": {
        "device":"VenusC",
        "ver":111,
        "ble_mac":"123456789012",
        "wifi_mac":"012123456789",
        "wifi_name"："MY_HOME",
        "ip":"192.168.1.11"
        }
    }
```

## **3.2 WiFi**

​		The WiFi component is mainly used for configuring the device's WiFi and obtaining the device's basic network information.

​		Wifi.GetStatus: Obtain the device's basic network information.

### **3.2.1 Wifi.GetStatus**

**Sending**:

| **Property (params)** | **Type** | **Description** |
| --------------------- | -------- | --------------- |
| id                    | *number* | ID of Instance  |

**Response**:

| **Property (result)** | **Type**         | **Description**      |
| --------------------- | ---------------- | -------------------- |
| id                    | *number*         | ID of Instance       |
| wifi_mac              | *string*         | WiFi MAC             |
| ssid                  | *string or null* | WiFi name            |
| rssi                  | *number*         | WiFi signal strength |
| sta_ip                | *string or null* | Device IP            |
| sta_gate              | *string or null* | Gateway              |
| sta_mask              | *string or null* | Subnet mask          |
| sta_dns               | *string or null* | DNS                  |

**Example:**

**Sending**:

```
    {
        "id": 1,
        "method": "Wifi.GetStatus",
        "params": {
        "id": 0
        }
    }
```

**Response**:

```
    {
        "id": 1,
        "src": "VenusC-mac",
        "result": {
        "id": 0,
        "wifi_mac": "620b0c877705"
        "ssid":"Hame",
        "rssi": -59,
        "sta_ip":"192.168.137.41",
        "sta_gate":"192.168.137.1",
        "sta_mask":"255.255.255.0",
        "sta_dns":"192.168.137.1"
        }
    }
```

## **3.3 Bluetooth**

​		The BLE (Bluetooth) component can view the Bluetooth-related information of the device

​		BLE.GetStatus: Check the Bluetooth connection status of the device.

### **3.3.1 BLE.GetStatus**

**Sending:**

| **Property (params)** | **Type** | **Description** |
| --------------------- | -------- | --------------- |
| id                    | *number* | ID of Instance  |

**Response**:

| **Property (result)** | **Type** | **Description** |
| --------------------- | -------- | --------------- |
| state                 | *string* | Bluetooth state |
| ble_mac               | *string* | Bluetooth MAC   |

**Example**

**Sending**:

```
    {
        "id": 1,
        "method": "BLE.GetStatus",
        "params": {
        "id": 0
        }
    }
```

**Response:**

```
    {
        "id": 1,
        "src": "VenusC-mac",
        "result": {
        "id": 0,
        "state":"connect",
        "ble_mac":"50cf14640fac"
        }
    }
```

## **3.4 Battery**

​		The Bat (Battery) component contains basic information about the device's battery

​	    **Bat**.**GetStatus**: Query the device's battery information and operating status.

### **3.4.1 Bat.GetStatus**

**Sending**:

| **Property (params)** | **Type** | **Description** |
| --------------------- | -------- | --------------- |
| id                    | *number* | ID of Instance  |

**Response**:

| **property (result)** | **Type**         | **Description**                  |
| --------------------- | ---------------- | -------------------------------- |
| id                    | *number*         | ID of Instance                   |
| soc                   | *string*         | soc                              |
| charg_flag            | *boolean*        | Charging permission flag         |
| dischrg_flag          | *boolean*        | Discharge permission flag        |
| bat_temp              | *number or null* | Battery temperature, [°C]        |
| bat_capacity          | *number or null* | Battery remaining capacity, [Wh] |
| rated_capacity        | *number or null* | Battery rated capacity, [Wh]     |

**Example**

**Sending**:

```
    {
        "id": 1,
        "method": "Bat.GetStatus",
        "params": {
        "id": 0
        }
    }
```

**Response**:

```
    {
        "id": 1,
        "src": "VenusC-mac",
        "result": {
        "id": 0,
        "soc": 90,
        "charg_flag": true,
        "dischrg_flag": true,
        "bat_temp": 25.0,
        "bat_capacity": 256.0,
        "rated_capacity": 2560.0
        }
    }
```

## **3.5 PV(only for Venus A/D)**

​		The PV (Photovoltaic) component contains the photovoltaic information connected to thedevice

​		**PV**.**GetStatus**: Query the device's connected photovoltaic information and power generationstatus

### **3.5.1 PV.GetStatus**

**Sending**:

| **Property (params)** | **Type** | **Description** |
| --------------------- | -------- | --------------- |
| id                    | *number* | ID of Instance  |

**Response**:

| **Property (result)** | **Type** | **Description**                      |
| --------------------- | -------- | ------------------------------------ |
| id                    | *number* | ID of Instance                       |
| pv_power              | *number* | Photovoltaic charging power, [W]     |
| pv_voltage            | *number* | Photovoltaic charging voltage, [V]   |
| pv_current            | *number* | Photovoltaic charging current, [A]   |
| PV_state              | *number* | Photovoltaic status 1:Work 0:Standby |

**Example**：

```
    {
        "id": 1,
        "method": "PV.GetStatus",
        "params": {
        "id": 0
        }
    }
```

**Response**:

```
    {
        "id": 1,
        "src": "VenusC-mac",
        "result": {
        "id": 0,
        "pv1_power": 0,
        "pv1_voltage": 10,
        "pv1_current": 0,
        "pv1_state": 0,
        "pv2_power": 0,
        "pv2_voltage": 9,
        "pv2_current": 0,
        "pv2_state": 0,
        "pv3_power": 0,
        "pv3_voltage": 10,
        "pv3_current": 0,
        "pv3_state": 0,
        "pv4_power": 0,
        "pv4_voltage": 10,
        "pv4_current": 0,
        "pv4_state": 0
        }
    }
```

## **3.6 ES**

​		The ES (Energy System) component contains the device's basic power information and energy statistics, and can configure or monitor the device's operating status.

1. ES.GetStatus: Query the device's basic electrical energy information.

2. ES.SetMode: Configure the device's operating mode.

3. ES.GetMode: Get information about the operating mode of the device.

### **3.6.1 ES.GetStatus**

**Sending**:

| **Property (params)** | **Type** | **Description** |
| --------------------- | -------- | --------------- |
| id                    | *number* | ID of Instance  |

**Response:**

| **Property (result)**    | **Type**         | **Description**                               |
| ------------------------ | ---------------- | --------------------------------------------- |
| id                       | number or *null* | ID of Instance                                |
| bat_soc                  | number or *null* | Total battery SOC，[%]                        |
| bat_cap                  | number or *null* | Total battery capacity, [Wh]                  |
| pv_power                 | number or *null* | Solar charging power, [W]                     |
| ongrid_power             | number or *null* | Grid-tied power, [W]                          |
| offgrid_power            | number or *null* | Off-grid power, [W]                           |
| bat_power                | number or *null* | Battery power, [W]                            |
| total\_pv_energy          | number or *null* | Total solar energy generated, [0.01*KWh]      |
| total\_grid\_output\_energy | number or *null* | Total grid output energy, [Wh]                |
| total\_grid\_input\_energy  | number or *null* | Total grid input energy, [Wh]                 |
| total\_load\_energy        | number or *null* | Total load (or off-grid) energy consumed,[Wh] |

**Example**：

**Sending**:

```
    {
        "id": 1,
        "method": "ES.GetStatus",
        "params": {
        "id": 0
        }
    }
```

**Response**:

```
    {
        "id": 1,
        "src": "VenusE-24215edb178f",
        "result": {
        "id": 0,
        "bat_soc": 98,
        "bat_cap": 5120,
        "pv_power": 0,
        "ongrid_power": 100,
        "offgrid_power": 0,
        "total_pv_energy": 0,
        "total_grid_output_energy": 2548,
        "total_grid_input_energy": 3273,
        "total_load_energy": 0
        }
    }
```

### **3.6.2 ES.SetMode**

**Sending**:

| **Property (params)** | **Type** | **Description**   |
| --------------------- | -------- | ----------------- |
| id                    | number   | ID of Instance    |
| config                | object   | Config Parameters |

**Object: config**

| **Property** **(config)** | **Type** | **Description**                                              |
| ------------------------- | -------- | ------------------------------------------------------------ |
| mode                      | *string* | Device power generation mode, including the following ："Auto"；"AI"；"Manual"；"Passive"; "Ups". |
| auto_cfg                  | *object* | Configuration parameters for Auto mode                       |
| ai_cfg                    | *object* | Configuration parameters for AI mode                         |
| manual_cfg                | *object* | Configuration parameters for Manual mode                     |
| passive_cfg               | *object* | Configuration parameters for Passive mode                    |
| ups_cfg                   | *object* | Configuration parameters for Ups mode                        |

**Object: auto_cfg**

| **Property (auto_cfg**) | **Type** | **Description**              |
| ----------------------- | -------- | ---------------------------- |
| enable                  | number   | ON: 1; OFF: Set another mode |

**Object: ai_cfg**

| **Property (ai_cfg**) | **Type** | **Description**              |
| --------------------- | -------- | ---------------------------- |
| enable                | number   | ON: 1; OFF: Set another mode |

**Object: manual_cfg**

| **Property** **(manual_cfg)** | **Type** | **Description**                                              |
| ----------------------------- | -------- | ------------------------------------------------------------ |
| time_num                      | number   | Time period serial number, Venus A/C/D/E  supports 0-9; Venus E mini supports 0-5            |
| start_time                    | *string* | Start time, hours: minutes, [hh:mm]                          |
| end_time                      | *string* | End time, hours: minutes, [hh:mm]                            |
| week_set                      | number   | Week, a byte 8 bits, the low 7 bits effective, the highest nvalid, 0000 0001 (1) on behalf of Monday open, 0000 0011 (3) on behalf of Monday and Tuesday open, |
| power                         | number   | Setting power,[W]                                            |
| enable                        | number   | ON: 1; OFF: 0                                                |
| manual_set (**Only for Venus E mini**) | number   | 0: disable; 1: enable charge; 2: enable discharge; 3: auto                                         |

**Object: passive_cfg**

| **Property (passive_cfg)** | **Type** | **Description**     |
| -------------------------- | -------- | ------------------- |
| power                      | number   | Setting power,[W]   |
| cd_time                    | number   | Power countdown,[s] |

**Object: ups_cfg**

| **Property (ups_cfg)** | **Type** | **Description** |
| ---------------------- | -------- | --------------- |
| enable                 | number   | ON: 1; OFF: 0   |

**Response**：

| **Property**（**result**） | **Type** | **Description**                                        |
| -------------------------- | -------- | ------------------------------------------------------ |
| id                         | number   | ID of Instance                                         |
| set_result                 | boolean  | "true":succeeded in setting; "false":failed in setting |

**Example**

**Sending**:

```
    /* Auto Mode Example */
    {
        "id": 1,
        "method": "ES.SetMode",
            "params": {
            "id": 0,
            "config": {
                "mode": "Auto",
                "auto_cfg": {
                "enable": 1
                }
            }
        }
    }
    
  /* AI Mode Example */
    {
        "id": 1,
        "method": "ES.SetMode",
        "params": {
        "id": 0,
            "config": {
            "mode": "AI",
                "ai_cfg": {
                "enable": 1
                }
            }
        }
    }  
  /* Manual Mode Example */
    {
        "id": 1,
        "method": "ES.SetMode",
            "params": {
            "id": 0,
                "config": {
                "mode": "Manual",
                    "manual_cfg": {
                    "time_num": 1,
                    "start_time": "08:30",
                    "end_time": "20:30",
                    "week_set": 127,
                    "power": 100,
                    "enable": 1,
                    "manual_set": 1
                    }
                }
            }
    }
    
	/* Passive Pattern Example */
    {
    "id": 1,
    "method": "ES.SetMode",
        "params": {
        "id": 0,
            "config": {
            "mode": "Passive",
                "passive_cfg": {
                "power": 100,
                "cd_time": 300
                }
            }
        }
    }    
    
	/* UPS Mode Example */
    {
    "id": 1,
    "method": "ES.SetMode",
        "params": {
        "id": 0,
            "config": {
            "mode": "UPS",
                "ups_cfg": {
                "enable": 1
                }
            }
        }
    }
```

**Response**:

```
    {
        "id": 1,
        "src": "Venus-mac",
        "result": {
        "id": 0,
        "set_result": ture
        }
    }
```

### **3.6.3 ES.GetMode**

**Sending**:

| **Property (params)** | **Type**         | **Description** |
| --------------------- | ---------------- | --------------- |
| id                    | *number or null* | ID of Instance  |

**Response**:

| **Property **(result) | **Type**           | **Description**                                              |
| --------------------- | ------------------ | ------------------------------------------------------------ |
| id                    | *number* *or null* | ID of Instance                                               |
| mode                  | *number* *or null* | Auto：Auto mode; AI: AI mode; Manual: manual mode; Passive: Passive control mode |
| ongrid_power          | *number* *or null* | Grid-tied power, [W]                                         |
| offgrid_power         | *number* *or null* | Off-grid power, [W]                                          |
| bat_soc               | *number* *or null* | SOC，[%]                                                     |
| ct_state              | *number* *or null* | CT Status，0: Not connected; 1: Connected. Note: Effective in Auto mode and AI mode. |
| a_power               | *number* *or null* | Phase A Power, [W] Note: Effective in Auto mode and AI mode  |
| b_power               | *number* *or null* | Phase B Power, [W] Note: Effective in Auto mode and AI mode  |
| c_power               | *number* *or null  | Phase C Power, [W] Note: Effective in Auto mode and AI mode  |
| total_power           | *number* *or null* | CT total power, [W] Note: Effective in automatic mode and AI mode |
| input_energy          | *number* *or null* | Cumulative Input Energy [Wh] （*0.1）Note: Effective in automatic mode and AI mode |
| output_energy         | *number* *or null* | Total cumulative output energy [Wh] （*0.1）Note: Effective in automatic mode and AI mode |

**Example**

**Sending**:

```
    {
        "id": 1,
        "method": "ES.GetMode",
        "params": {
        "id": 0
        }
    }
```

**Response**:

```
    {
        "id": 1,
        "src": "VenusE-24215edb178f",
        "result": {
        "id": 0,
        "mode": "Auto",
        "ongrid_power": 100,
        "offgrid_power": 0,
        "bat_soc": 98,
        "ct_state": 0,
        "a_power": 0,
        "b_power": 0,
        "c_power": 0,
        "total_power": 0
        "input_energy": 3086320,
        "output_energy": 4487510
        }
    }
```

## **3.7 EM**

​		The Energy Meter (EM) module contains status information and power measurement data from the energy meter, or data obtained from the current transformer (CT).

​		EM.GetStatus: Queries the basic status and data information of the energy meter.

### **3.7.1 EM.GetStatus**

**Sending:**

| **Property (params)** | **Type**         | **Description** |
| --------------------- | ---------------- | --------------- |
| id                    | *number or null* | ID of Instance  |

**Response**：

| **Property** **(result)** | **Type**           | **Description**                                              |
| ------------------------- | ------------------ | ------------------------------------------------------------ |
| id                        | *number or* *null* | ID of Instance                                               |
| ct_state                  | *number or* *null* | CT (Current Transformer) status: 0: Not connected 1: Connected |
| a_power                   | *number or* *null  | Phase A power,[W]                                            |
| b_power                   | *number or* *null* | Phase B power,[W]                                            |
| c_power                   | *number or* *null* | Phase C power,[W]                                            |
| total_power               | *number or* *null* | Total power,[W]                                              |
| input_energy              | *number or* *null* | Cumulative Input Energy [0.1 Wh]                             |
| output_energy             | *number or* *null* | Total cumulative output energy [0.1 Wh]                      |

**Sending**:

```
    {
        "id": 1,
        "method": "EM.GetStatus",
        "params": {
        "id": 0
        }
    }
```

**Response**:

```
    {
        "id": 1,
        "src": "VenusD-009b08a5ac28",
        "result": {
        "id": 0,
        "ct_state": 1,
        "a_power": 0,
        "b_power": 0,
        "c_power": 0,
        "total_power": 0,
        "input_energy": 0,
        "output_energy": 0
        }
    }
```

## **3.8 SYS**

​		Includes some common system control commands, like DOD and Bluetooth lock features

### 3.8.1 DOD

​		This chapter contains the DOD configuration instructions. The device's default DOD value is 88, with a setting range of 30-88.

**Sending**:

| **Property (params)** | **Type**         | **Description**         |
| --------------------- | ---------------- | ----------------------- |
| id                    | *number or null* | ID of Instance          |
| Value                 | number           | DOD Value (range 30-88) |

**Response**：

| **Property**（**result**） | **Type** | **Description**          |
| -------------------------- | -------- | ------------------------ |
| id                         | number   | ID of Instance           |
| set_result                 | boolean  | true：Success false：fai |

**Sending:**

```
    {
        "id": 0,
        "method": "DOD.SET",
        "params": {
        "value": 36
        }
    }
```

**Response**：

```
    {
        "id": 1,
        "src": "Venus-mac",
        "result": {
        "id": 0,
        "set_result": ture
        }
    }
```

### 3.8.2 **Ble_block**

​		This chapter covers the Bluetooth lock feature, including turning Bluetooth broadcasting on and off

**Sending**:

| **Property (params)** | **Type**         | **Description**      |
| --------------------- | ---------------- | -------------------- |
| id                    | *number or null* | ID of Instance       |
| enable                | number           | 0：enable 1：disable |

**Response**：

| **Property**（**result**） | **Type** | **Description**          |
| -------------------------- | -------- | ------------------------ |
| id                         | number   | ID of Instance           |
| set_result                 | boolean  | true：Success false：fai |

**Sending**:

```
    {
        "id": 0,
        "method": "Ble.Adv",
        "params": {
        "enable": 0
        }
    }
```

**Response**：

```
    {
        "id": 1,
        "src": "Venus-mac",
        "result": {
        "id": 0,
        "set_result": ture
        }
    }
```

### 3.8.3 **Led_Ctrl**

​		This chapter covers the LED functions of the switchgear panel:

**Sending**:

| **Property (params)** | **Type**         | **Description** |
| --------------------- | ---------------- | --------------- |
| id                    | *number or null* | ID of Instance  |
| state                 | number           | 1：on 0：off    |

**Sending**:

```
    {
        "id": 0,
        "method": "Led.Ctrl",
        "params": {
        "state": 0
        }
    }
```

**Response:**

```
    {
        "id": 1,
        "src": "Venus-mac",
        "result": {
        "id": 0,
        "set_result": ture
        }
    }
```

### 3.8.4  **Version_Set**

​		This chapter includes settings for charge and discharge power version information

| **Property (params)** | **Type**         | **Description**                                        |
| --------------------- | ---------------- | ------------------------------------------------------ |
| id                    | *number or null* | ID of Instance                                         |
| version               | number           | 800W, 1200W(VA), 1500W（VA）,2200W(VD)，and2500W(VE)。 |

**Example**

**Sending**:

```
    {
      "id": 0,
      "method": "Set.Ver",
          "params": {
            "version": 2500
          }
    }
```

**Response:**

```
    {
        "id": 1,
        "src": "Venus-mac",
        "result": {
            "id": 0,
            "set_result": ture
        }
    }
```

### 3.8.5 **Factory_Reset**

​		This chapter includes information on resetting to factory settings

| **Property (params)** | **Type**         | **Description**                                              |
| --------------------- | ---------------- | ------------------------------------------------------------ |
| id                    | *number or null* | ID of Instance                                               |
| type                  | number           | 1. Reset to factory settings, clear accumulated data<br/>2. Reset to factory settings, keep accumulated data |

**Example**

**Sending**:

```
    {
      "id": 0,
      "method": "Reset.Factory",
          "params": {
            "type": 1
          }
    }
```

**Response:**

```
    {
        "id": 1,
        "src": "Venus-mac",
        "result": {
            "id": 0,
            "set_result": ture
        }
    }
```

# **IV . Devices**

​		This chapter will describe the extent of support for the components and services in this API  documentation by different Marstek devices, as well as some proprietary information.

## **4.1 Venus C/E**

1. Marstek

2. WiFi

3. Bluetooth

4. Battery

5. ES

6. EM

7. SYS（FW>=150 available）

## **4.2 Venus D/Venus A**

1. Marstek

2. WiFi

3. Bluetooth

4. Battery

5. PV

6. ES

7. EM

8. SYS（FW>=150 available）

## **4.3 Venus E mini**

1. Marstek

2. WiFi

3. Bluetooth

4. Battery

5. ES

6. EM

8. SYS（DOD、Ble\_block、Led\_Ctrl）