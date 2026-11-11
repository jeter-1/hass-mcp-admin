"""HAMCP-319 section 6.5 (DCL2): compiled ``ha_get_device`` allowlist projection.

The projection is the only output of a reviewed ``ha_get_device`` read. It
passes named fields only: an unknown key, a non-scalar value where a scalar is
named, a registry pair outside the positive pair allowlist or a derived
identity field that fails its compiled check is dropped and disclosed by a
closed code plus a bounded count. A disclosure never names a key, a
discriminator, a value, an index, a length or a reason subtype. No Thread,
Matter, Zigbee or Z-Wave dataset or credential material can pass because
nothing unnamed passes. Upstream warning text is mapped to closed codes and
never passed. The projection performs no I/O and writes no log record.

The rule is version-independent and compiled for the exact reviewed 8.5.0 and
9.1.0 adapters, whose device builders are identical (ha-mcp 8.5.0
``tools_registry.py:128-185,545-605`` and 9.1.0 ``tools_registry.py:132-186,
424-485`` with ``device_enrichment.py:11-133``). Any structurally unrecognized
result raises ``ValueError``; the gateway maps it to ``invalid_response``.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

# HAMCP-351: 9.1.2 (bab615df) leaves tools_registry.py and device_enrichment.py
# unchanged from 9.1.0.
REVIEWED_VERSIONS = frozenset({"8.5.0", "9.1.0", "9.1.2"})

# Closed disclosure codes. Nothing else is ever written to ``warnings``.
REGISTRY_PAIR_WITHHELD = "registry_pair_withheld"
REGISTRY_IDENTITY_WITHHELD = "registry_identity_withheld"
THREAD_MESH_LOCAL_WITHHELD = "thread_mesh_local_withheld"
ZHA_RADIO_METRICS_UNAVAILABLE = "zha_radio_metrics_unavailable"
ZWAVE_NODE_STATUS_UNAVAILABLE = "zwave_node_status_unavailable"
MATTER_NODE_DIAGNOSTICS_UNAVAILABLE = "matter_node_diagnostics_unavailable"
ENRICHMENT_WARNING_UNRECOGNIZED = "enrichment_warning_unrecognized"
UNKNOWN_FIELDS_WITHHELD = "unknown_fields_withheld"
CLOSED_WARNING_CODES = frozenset(
    {
        REGISTRY_PAIR_WITHHELD,
        REGISTRY_IDENTITY_WITHHELD,
        THREAD_MESH_LOCAL_WITHHELD,
        ZHA_RADIO_METRICS_UNAVAILABLE,
        ZWAVE_NODE_STATUS_UNAVAILABLE,
        MATTER_NODE_DIAGNOSTICS_UNAVAILABLE,
        ENRICHMENT_WARNING_UNRECOGNIZED,
        UNKNOWN_FIELDS_WITHHELD,
    }
)

COUNT_CAP = 999
PAIR_EXAMINE_LIMIT = 64
PAIR_VALUE_MAX = 64
FABRIC_LIMIT = 64
FABRIC_TEXT_CAP = 64

# Upstream enrichment prefixes (ha-mcp 9.1.0 ``device_enrichment.py:34,47,75,
# 88,120,133``; identical in 8.5.0 ``tools_registry.py``). Only the closed code
# is emitted; the remainder of the upstream text is discarded.
_WARNING_PREFIXES = (
    ("ZHA radio metrics unavailable: ", ZHA_RADIO_METRICS_UNAVAILABLE),
    ("Z-Wave node status unavailable: ", ZWAVE_NODE_STATUS_UNAVAILABLE),
    ("Matter node diagnostics unavailable: ", MATTER_NODE_DIAGNOSTICS_UNAVAILABLE),
)

# Fixed list-mode hints (ha-mcp 9.1.0 ``tools_registry.py:546-557``; identical
# in 8.5.0). Any other text is withheld.
_USAGE_HINTS = frozenset(
    {
        "Use 'friendly_name' for MQTT topics: zigbee2mqtt/{friendly_name}/action",
        "Use 'ieee_address' for zha_event triggers in automations",
        "Use node_id for Z-Wave device identification. "
        "Single device lookup includes node status (security, routing).",
    }
)

_A = re.ASCII
_EUI64_COLON = r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){7}"
_EUI64_HEX = r"0x[0-9A-Fa-f]{16}"
_EUI64 = re.compile(rf"(?:{_EUI64_COLON}|{_EUI64_HEX})", _A)
_NODE_ID = re.compile(r"[0-9]{1,4}", _A)

# Positive registry-pair allowlists (design section 6.5). Exact,
# case-sensitive discriminator; anchored ASCII full match on the value.
CONNECTION_FORMATS: dict[str, re.Pattern[str]] = {
    "mac": re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", _A),
    "bluetooth": re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", _A),
    "zigbee": re.compile(_EUI64_COLON, _A),
    "upnp": re.compile(
        r"uuid:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}",
        _A,
    ),
    "ieee": re.compile(rf"(?:{_EUI64_COLON}|{_EUI64_HEX})", _A),
}
IDENTIFIER_FORMATS: dict[str, re.Pattern[str]] = {
    "zha": re.compile(_EUI64_COLON, _A),
    "zwave_js": re.compile(
        r"[0-9]{1,10}-[0-9]{1,4}"
        r"|[0-9]{1,10}-[0-9]{1,4}-[0-9]{1,3}"
        r"|[0-9]{1,10}-[0-9]{1,4}-[0-9]{1,5}:[0-9]{1,5}:[0-9]{1,5}",
        _A,
    ),
    "matter": re.compile(
        r"deviceid_[0-9A-F]{16}-[0-9A-F]{16}-(?:MatterNodeDevice|[0-9]{1,5})", _A
    ),
    "mqtt": re.compile(r"zigbee2mqtt_(?:bridge_)?0x[0-9A-Fa-f]{16}", _A),
    "hassio": re.compile(r"core|supervisor|host|OS", _A),
}

# Home Assistant Core 2026.11.0 (commit 74cf5a49c63ed8d4d87717844bf14f79c28c9cea)
# manifest domains: every ``homeassistant/components/<d>/manifest.json``, each
# ``domain`` equal to its directory name. SHA-256 of the sorted,
# newline-terminated list is CORE_DOMAIN_SET_SHA256. A mismatch empties the set,
# so every integration field fails closed.
CORE_DOMAIN_SET_COUNT = 1562
CORE_DOMAIN_SET_SHA256 = "5a77c4c12f8727e59d35b54ca40744de4590c3ce5cb2cba76d4f9a6642da7743"
_CORE_DOMAINS_TEXT = """
    3_day_blinds abode abrp acaia accuweather acmeda acomax actiontec actron_air
    adax adguard ads advantage_air aemet aep_ohio aep_texas aerosmart aftership
    agent_dvr ai_task aidot air_quality airgradient airly airnow airobot airos
    airpatrol airq airthings airthings_ble airtouch4 airtouch5 airvisual
    airvisual_pro airzone airzone_cloud aladdin_connect alarm_control_panel
    alarmdecoder alert alexa alexa_devices alpha_bidet_infrared alpha_vantage
    altruist amazon_polly amberelectric ambient_network ambient_station amcrest
    amp_motorization analytics analytics_insights android_ip_webcam androidtv
    androidtv_remote anel_pwrctrl anglian_water anova anthemav anthropic
    anwb_energie any_list aosmith apache_kafka apcupsd api apollo_automation
    appalachianpower apple_tv application_credentials apprise aprilaire aprs
    apsystems aquacell aqualogic aquostv aqvify aranet arcam_fmj arest ariston
    arris_tg2492lg artsound aruba arve arwn aseko_pool_live assist_pipeline
    assist_satellite asuswrt atag atlanticcityelectric atome august august_ble
    aurora aurora_abb_powerone aussie_broadband austroflamm autarco auth
    automation autoskope avea avosdim awair aws aws_s3 axis axle_energy
    azure_data_explorer azure_devops azure_event_hub azure_service_bus
    azure_storage backblaze_b2 backup baf baidu balay balboa bang_olufsen
    battery bauknecht bayesian bbox besen beverly bge binary_sensor bitcoin
    bitvis bizkaibus blackbird blebox blink bliss_automation bloc_blinds
    blockchain blue_current bluemaestro blueprint bluesound bluetooth
    bluetooth_adapters bluetooth_le_tracker bluetti_modbus bmw_connected_drive
    bond bosch_alarm bosch_shc bosch_smart_camera brands brandt braviatv
    brel_home bring broadlink brother brottsplatskartan browser brunt
    bryant_evolution bsblan bswitch bt_home_hub_5 bt_smarthub bthome bticino
    bubendorff bugu buienradar burbank_water_and_power button bwt caldav
    calendar cambridge_audio camera canary carrier casper_glow cast ccm15
    centriconnect cert_expiry chacon_dio channels chef_iq chess_com chirpstack
    cielo_home cisco_ios cisco_mobility_express cisco_webex_teams citybikes
    clickatell clicksend clicksend_tts climate cloud cloudflare cloudflare_r2
    cmus co2signal coautilities coinbase collection_image colmo color_extractor
    comed comed_hourly_pricing comelit comfee comfoconnect command_line
    compensation compit concord232 coned config configurator constructa control4
    conversation cookidoo coolmaster cosori counter cover cozytouch cppm_tracker
    cpuspeed cribl crownstone currencylayer cync dacia daikin danfoss_air
    data_grand_lyon datadog date datetime ddwrt deako debug_console debugpy
    deconz decora_wifi decorquip default_config delijn delmarva deluge demo
    denon denon_rs232 denonavr derivative devialet device_automation
    device_sun_light_trigger device_tracker devolo_home_control
    devolo_home_network dexcom dhcp diagnostics dialogflow diaz digital_loggers
    digital_ocean directv discogs discord discovergy dlink dlna_dmr dlna_dms
    dnsip doods door doorbell doorbird dooya dormakaba_dkey downloader
    dremel_3d_printer drop_connect dropbox droplet dsmr dsmr_reader duckdns duco
    dunehd duotecno duquesne_light dwd_weather_warnings dynalite dyson_infrared
    eafm earn_e_p1 eastron easyenergy ebox ebusd ecoal_boiler ecobee ecoforest
    econet ecosmart ecospar ecovacs ecowitt edifier_infrared edimax edl21 efergy
    egardia egauge eheimdigital eight_sleep ekeybionyx electrasmart
    electric_kiwi elevenlabs elgato elkm1 elmax elv elvia emby emoncms
    emoncms_history emonitor emulated_hue emulated_kasa emulated_roku
    energenie_power_sockets energie_vanons energieleser energy energyid
    energyzero engie_be enigma2 enocean enphase_envoy entur_public_transport
    envertech_evt800 environment_canada envisalink ephember epic_games_store
    epion epson eq3btsmart escea esera_onewire esphome essent eufy eufylife_ble
    eurotronic_cometblue event evergy everlights everything_smart_home
    evil_genius_labs evohome exicom_spin_ble ezviz faa_delays facebook fail2ban
    familyhub fan fastdotcom feedreader ffmpeg ffmpeg_motion ffmpeg_noise fibaro
    fido file file_upload filesize filter fing fints fire_tv firefly_iii
    fireservicerota firmata fish_audio fitbit fivem fixer fjaraskupan fleetgo
    flexit flexit_bacnet flexom flic flic_button flipr flo flock flow_it flume
    fluss flux flux_led folder folder_watcher foobot foreca forecast_solar
    forked_daapd fortios foscam foursquare frankever free_mobile freebox freedns
    freedompro freshr fressnapf_tracker fritz fritzbox fritzbox_callmonitor
    fronius frontend frontier_silicon fuelprices_dk fujitsu_anywair
    fujitsu_fglair fully_kiosk fumis futurenow fyta gaggenau garadget
    garage_door garages_amsterdam gardena_bluetooth gate gatus gaviota gc100
    gdacs generic generic_hygrostat generic_thermostat geniushub gentex_homelink
    geo_json_events geo_location geo_rss_events geocaching geofency
    geonetnz_quakes geonetnz_volcano geosphere_austria_warnings ghost gios
    github gitlab_ci gitter glances go2rtc goalzero gogogate2 goodwe google
    google_air_quality google_assistant google_assistant_sdk google_cloud
    google_drive google_generative_ai_conversation google_health google_mail
    google_maps google_photos google_pubsub google_sheets google_tasks
    google_translate google_travel_time google_weather google_wifi govee_ble
    govee_light_local gpsd gpslogger grandstream_home graphite gree
    gree_infrared green_planet_energy greencell greeneye_monitor group
    growatt_server gtfs guardian guntamatic haas_sohn habitica hanna harbor
    hardkernel hardware harman_kardon_avr harman_luxury harmony harvey hase_iq
    hassio havana_shade haveibeenpwned haven_iaq hddtemp hdfury hdmi_cec
    heatmiser hegel heicko heiwa helty helty_cloud heos here_travel_time heta
    hexaom hi_kumo hikvision hikvisioncam hisense_aehw4a1 history history_stats
    hitron_coda hive hko hlk_sw16 holiday home_connect home_plus_control
    homeassistant homeassistant_alerts homeassistant_connect_zbt2
    homeassistant_green homeassistant_hardware homeassistant_sky_connect
    homeassistant_yellow homee homekit homekit_controller homematic
    homematicip_cloud homevolt homewizard homeworks honeywell
    honeywell_string_lights horizon hortimax hotspring hp_ilo hp_printer
    hr_energy_qube html5 http huawei_lte hue hue_ble huisbaasje humidifier
    humidity hunterdouglas_powerview hurrican_shutters_wholesale
    husqvarna_automower husqvarna_automower_ble huum hvv_departures hydrawise
    hydroquebec_peak hyperion hypontech ialarm iammeter iaqualink ibeacon icloud
    idasen_desk idrive_e2 idteck_prox ifttt iglo igloohome ign_sismologia ihc
    illuminance image image_processing image_upload imap imeon_inverter imgw_pib
    immich imou improv_ble incomfort indevolt indi_allsky indianamichiganpower
    inels influxdb infrared inkbird input_boolean input_button input_datetime
    input_number input_select input_text inspired_shades insteon integration
    intelliclima intellifire intent intent_script intesishome inventor iometer
    ios iotawatt iotorero iotty iperf3 ipma ipp iqvia irish_rail_transport
    irm_kmi iron_os isal iseo_argo_ble iskra islamic_prayer_times ismartwindow
    israel_rail iss ista_ecotrend isy994 itach itunes ituran izone jellyfin
    jewish_calendar joaoapps_join juicenet justnimbus jvc_projector kaco_modbus
    kaco_rs485 kaiser_nienhaus kaiterra kaleidescape kankun karakeep keba
    keenetic_ndms2 kef kegtron kentuckypower keyboard_remote keymitt_ble kiosker
    kira kitchen_sink kiwi klik_aan_klik_uit kmtronic knocki knx kodi konnected
    konnected_esphome kostal_plenticore kraken krispol kulersky kwb labs
    lacrosse lacrosse_view lamarzocco lametric landisgyr_heat_meter lannouncer
    lastfm launch_library laundrify lawn_mower lcn ld2410_ble leaone led_ble
    led_infrared legrand lektrico letpot levoit lg_infrared lg_netcast
    lg_soundbar lg_thinq lg_tv_rs232 libre_hardware_monitor librenms libresync
    lichess lidarr liebherr life360 lifx lifx_cloud light lightwave limitlessled
    linak linkedgo linkplay linksys_smart linode linux_battery linx litejet
    litellm litterrobot little_swan livisi llama_cpp llamalab_automate llm
    local_calendar local_file local_ip local_todo locative lock logbook logger
    lojack london_air london_underground lookin loqed lorawan lovelace luci
    luftdaten lunatone lupusec lutron lutron_caseta luxaflex lw12wifi lyngdorf
    lyric madeco madvr mailgun manual manual_mqtt map_tiles marantz
    marantz_infrared marantz_rs232 marketplace martec marytts mastodon matrix
    matter maxcube maytag mazda mcp mcp_server mealie meater medcom_ble
    media_extractor media_player media_source mediaroom melcloud melcloud_home
    melissa melnor meraki message_bird met met_eireann meteo_france meteo_lt
    meteoalarm meteoclimatic metoffice mfi microbees microsoft midea miele
    mijn_farmad_apotheek mijndomein_energie mikrotik mill min_max
    minecraft_server minio mitsubishi_comfort mjpeg moat mobile_app mochad
    modbus modem_callerid modern_forms moehlenhoff_alpha2 moisture
    mold_indicator monarch_money monessen monoprice monzo moon mopeka motion
    motion_blinds motionblinds_ble motioneye motionmount mpd mqtt
    mqtt_eventstream mqtt_json mqtt_room mqtt_statestream msteams mta mullvad
    music_assistant mutesync mvglive my my_pv myneomitis myq mysensors mystrom
    mythicbeastsdns myuplink nad nam namecheapdns nanoleaf nasweb nature_remo
    neato nederlandse_spoorwegen neff neo neopool ness_alarm nest netatmo
    netdata netgear netgear_lte netio netsu network neurio_energy nexblue nexen
    nexia nexity nextbus nextcloud nextdns nfandroidtv nibe_heatpump nice_go
    nightscout niko_home_control nilu nina nintendo_parental_controls
    nissan_leaf nmap_tracker nmbs no_ip noaa_tides nobo_hub nordpool norway_air
    notify notify_events notion novy_cooker_hood nrgkick nsw_fuel_station
    nsw_rural_fire_service_feed ntfy nuheat nuki numato number nut nutrichef nws
    nx584 nyt_games nzbget oasa_telematics obihai occupancy octoprint oem
    ogemray ogeneral ohmconnect ohme okok_scale olimpia_splendid ollama ombi
    omie omnilogic onboarding oncue ondilo_ico onedrive onedrive_for_business
    onewire onida_infrared onkyo onvif open_meteo open_router
    openai_conversation openalpr_cloud opendisplay openerz openevse
    openexchangerates opengarage openhardwaremonitor openhome openrgb
    opensensemap opensky opentherm_gw openuv openweathermap opnsense opower
    opple oralb oru_opower orvibo osoenergy osram_infrared osramlightify otbr
    otp ouman_eh_800 ourgroceries overkiz overseerr ovhcloud_ai_endpoints
    ovo_energy owntracks p1_monitor paj_gps palazzetti panasonic_bluray
    panasonic_viera panel_custom paperless_ngx pcs_lighting peblar peco
    peco_opower pegel_online pencom pepco permobil persang_infrared
    persistent_notification person pge pglab philips_infrared philips_js pi_hole
    picnic picotts pilight pinecil ping pioneer piper pitsos pjlink plaato plant
    playstation_network plex plugwise plum_lightpad pocketcasts point pooldose
    poolsense portainer power powerfox powerfox_local powerwall prana
    private_ble_device pro_breeze probe_plus profiler profilo progettihwsw
    proliphix prometheus prosegur prowl proximity proxmoxve proxy prusalink ps4
    pse psoklahoma ptdevices pterodactyl pulseaudio_loopback pure_energie
    purpleair push pushbullet pushover pushsafer pvoutput pvpc_hourly_pricing
    pyload python_script qbittorrent qbus qingping qld_bushfire qnap qnap_qsw
    qrcode quadrafire quantum_gateway qvr_pro qwikswitch rabbitair rachio radarr
    radio_browser radio_frequency radiotherm rainbird rainbowminer
    rainforest_eagle rainforest_raven rainmachine random rapt_ble raspberry_pi
    raspyrfm raven_rock_mfg rdw recollect_waste recorder recovery_mode recswitch
    reddit redgtech refoss rehlko rejseplanen remember_the_milk remote
    remote_calendar remote_rpi_gpio renault renson reolink repairs repetier rest
    rest_command rexel rflink rfxtrx rhasspy ridwell ring ripple risco
    rituals_perfume_genie rmvtransport roborock rocketchat roku romy roomba roon
    rotenso route53 route_b_smart_meter rova rpi_power rss_feed_template
    rtorrent ruckus_unleashed russound_rio russound_rnet ruuvi_gateway
    ruuvitag_ble rympro sabnzbd saj samsam samsung_exlink samsung_infrared
    samsungtv sanix satel_integra saunum scene schedule schlage schluter scl
    scorpiontrack scrape screenaway screenlogic script seafile search season
    select sendgrid sense sensecap_lorawan sensibo sensirion_ble sensor
    sensorblue sensorpro sensorpush sensorpush_cloud sensoterra sentry senz
    serial serial_pm sesame seven_segments seventeentrack sfr_box sftp_storage
    sharkiq shell_command shelly shodan shopping_list sia siemens sighthound
    signal_messenger silla_prism silvercrest_infrared simplefin simplepush
    simplisafe simply_automated simu sinch siren sisyphus skoda sky_hub
    sky_remote skybeacon skybell slack sleep_as_android sleepiq slide
    slide_local slimproto sma smappee smarla smart_blinds smart_home
    smart_meter_texas smart_rollos smarther smartthings smarttub smarty
    smartyplants smhi smlight smtp smud snapcast snmp snoo snooz sofar solaredge
    solaredge_modbus solarlog solarman solax solyx_energy_cloud soma somfy
    somfy_mylink sonarr songpal sonos sony_projector soundtouch spaceapi spc
    specialized_turbo speedtestdotnet spider splunk spotify sql squeezebox
    srp_energy ssdp starline starlingbank starlink startca statistics statsd
    steam_online steamist steamvr_base_station steinway_lyngdorf stiebel_eltron
    stookwijzer stream streamlabswater stt subaru suez_water sun sunricher_dali
    sunsynk sunweg supervisord supla surepetcare swepco swiss_hydrological_data
    swiss_public_transport swisscom switch switch_as_x switchbee switchbot
    switchbot_cloud switcher_kis switchmate symfonisk syncthing syncthru
    synology_chat synology_dsm synology_srm syslog system_bridge system_health
    system_log systemmonitor systemnexa2 tado tag tailscale tailwind tami4
    tank_utility tankerkoenig tapsaff tasmota tautulli tcp technove ted5000
    tedee telegram telegram_bot teleinfo tellduslive tellstick telnet teltonika
    temper temperature template tesla_fleet tesla_wall_connector teslemetry
    tessie text theben_conexa thermador thermobeacon thermoplus thermopro
    thethingsnetwork thethingsnetwork_lorawan thingspeak thinkingcleaner thomson
    thread threema threshold tibber tile tilt_ble tilt_pi time time_date timer
    tmb tod todo todoist togrill tolo tomato tomorrowio tonewinner toon torque
    toshiba_lifestyle totalconnect touchline touchline_sl tplink tplink_lte
    tplink_omada tplink_tapo traccar traccar_server trace tractive tradfri
    trafikverket_camera trafikverket_ferry trafikverket_train
    trafikverket_weatherstation trane transmission transport_nsw trend
    triggercmd trmnl tts tuya twentemilieu twilio twilio_call twilio_sms twinkly
    twitch twitter ubiwizz ublockout ubus uhoo uk_transport ukraine_alarm
    ultraloq unifi unifi_access unifi_direct unifi_discovery unifiprotect
    universal upb upc_connect upcloud update upnp uprise_smart_shades uptime
    uptime_kuma uptimerobot usage_prediction usb usgs_earthquakes_feed
    utility_meter uvc v2c vacuum vagner_pool vallox valve vandelo vasttrafik
    vegehub velbus velux venstar vera verisure vermont_castings versasense
    version vesync viaggiatreno vibration vicare victron_ble victron_gx
    victron_remote_monitoring vilfo vistapool vitesy vivotek vizio vlc_telnet
    vodafone_station voicerss voip volkszaehler volumio volvo w800rf32 wahin
    wake_on_lan wake_word wallbox waqi water_heater waterfurnace watergate watts
    watttime wattwaechter waze_travel_time weather weatherflow weatherflow_cloud
    weatherkit web_rtc webdav webhook webmin webostv websocket_api weheat wemo
    whirlpool whisper whois wiffi wiim wilight willow window wirelesstag
    withings wiz wled wmspro wolflink workday worldclock worldtidesinfo
    worxlandroid ws66i wsdot wyoming x10 xbox xeoma xiaomi xiaomi_aqara
    xiaomi_ble xiaomi_miio xiaomi_tv xiaomi_weather xmpp xs1 xthings_cloud yale
    yale_smart_alarm yalexs_ble yamaha yamaha_musiccast yandex_transport
    yandextts yardian yeelight yeelightsunflower yi yolink yoto youless youtube
    zabbix zamg zbox_hub zengge zeroconf zerproc zeversolar zha zhong_hong
    ziggo_mediabox_xl zimi zinvolt zodiac zondergas zone zoneminder zonneplan
    zwave_js zwave_me
"""


def _compile_domain_set() -> frozenset[str]:
    names = sorted(set(_CORE_DOMAINS_TEXT.split()))
    digest = hashlib.sha256(("\n".join(names) + "\n").encode("ascii")).hexdigest()
    if len(names) != CORE_DOMAIN_SET_COUNT or digest != CORE_DOMAIN_SET_SHA256:
        return frozenset()
    return frozenset(names)


CORE_INTEGRATION_DOMAINS = _compile_domain_set()
_INTEGRATION_TYPE_EXTRA = frozenset({"zigbee2mqtt", "unknown"})


class _Disclosure:
    """Per-result counters; never records a name, value or index."""

    def __init__(self) -> None:
        self.codes: set[str] = set()
        self.unknown = 0

    def drop(self, count: int = 1) -> None:
        self.unknown += count
        self.codes.add(UNKNOWN_FIELDS_WITHHELD)


def _cap(value: int) -> int:
    return min(value, COUNT_CAP)


def _is_text(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, bool)) or _is_number(value)


def _text_or_none(value: Any) -> bool:
    return value is None or _is_text(value)


def _int_or_none(value: Any) -> bool:
    return value is None or _is_int(value)


def _number_or_none(value: Any) -> bool:
    return value is None or _is_number(value)


def _bool_or_none(value: Any) -> bool:
    return value is None or isinstance(value, bool)


def _pick(source: dict[str, Any], spec: dict[str, Any], out: dict[str, Any], d: _Disclosure) -> None:
    for name, check in spec.items():
        if name in source:
            if check(source[name]):
                out[name] = source[name]
            else:
                d.drop()


def _count_unknown(source: dict[str, Any], known: frozenset[str], d: _Disclosure) -> None:
    extra = sum(1 for name in source if name not in known)
    if extra:
        d.drop(extra)


def _text_list(value: Any, d: _Disclosure) -> list[str] | None:
    if not isinstance(value, list):
        d.drop()
        return None
    kept = [item for item in value if _is_text(item)]
    if len(kept) != len(value):
        d.drop(len(value) - len(kept))
    return kept


def _capped_text(value: Any) -> Any:
    if value is None:
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable())
    return cleaned[:FABRIC_TEXT_CAP]


def project_registry_pairs(value: Any, formats: dict[str, re.Pattern[str]]) -> tuple[list[list[str]], int]:
    """Return ``(passing pairs, withheld count)`` for one registry pair array."""

    if not isinstance(value, list):
        return [], 1
    withheld = max(len(value) - PAIR_EXAMINE_LIMIT, 0)
    kept: list[list[str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in value[:PAIR_EXAMINE_LIMIT]:
        if (
            not isinstance(entry, list)
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not isinstance(entry[1], str)
        ):
            withheld += 1
            continue
        discriminator, pair_value = entry
        pattern = formats.get(discriminator)
        if (
            pattern is None
            or not 1 <= len(pair_value) <= PAIR_VALUE_MAX
            or pattern.fullmatch(pair_value) is None
        ):
            withheld += 1
            continue
        key = (discriminator, pair_value)
        if key not in seen:
            seen.add(key)
            kept.append([discriminator, pair_value])
    return kept, withheld


_ENTITY_ROW = {"entity_id": _is_text, "name": _text_or_none, "platform": _text_or_none}
_ENTITY_KEYS = frozenset(_ENTITY_ROW)


def _entities(value: Any, d: _Disclosure) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        d.drop()
        return None
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            d.drop()
            continue
        row: dict[str, Any] = {}
        _pick(item, _ENTITY_ROW, row, d)
        _count_unknown(item, _ENTITY_KEYS, d)
        if "entity_id" not in row:
            d.drop()
            continue
        rows.append(row)
    return rows


_RADIO_METRICS = {"lqi": _number_or_none, "rssi": _number_or_none}
_NODE_STATUS = {
    "node_id": _int_or_none,
    "status": _is_scalar,
    "is_routing": _bool_or_none,
    "is_secure": _is_scalar,
    "highest_security_class": _is_scalar,
    "zwave_plus_version": _int_or_none,
    "is_controller_node": _bool_or_none,
}
_NODE_DIAGNOSTICS = {
    "network_type": _text_or_none,
    "node_type": _text_or_none,
    "available": _bool_or_none,
    "network_name": _text_or_none,
    "mac_address": _text_or_none,
    "active_fabric_index": _int_or_none,
}
_NODE_DIAGNOSTICS_KEYS = frozenset(_NODE_DIAGNOSTICS) | {"ip_addresses", "active_fabrics"}
_FABRIC = {
    "fabric_index": _int_or_none,
    "fabric_id": _int_or_none,
    "vendor_id": _int_or_none,
    "vendor_name": _text_or_none,
    "fabric_label": _text_or_none,
}
_FABRIC_KEYS = frozenset(_FABRIC)
_IP_NETWORK_TYPES = frozenset({"wifi", "ethernet"})


def _flat(value: Any, spec: dict[str, Any], d: _Disclosure) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        d.drop()
        return None
    out: dict[str, Any] = {}
    _pick(value, spec, out, d)
    _count_unknown(value, frozenset(spec), d)
    return out


def _node_diagnostics(value: Any, d: _Disclosure) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        d.drop()
        return None
    out: dict[str, Any] = {}
    _pick(value, _NODE_DIAGNOSTICS, out, d)
    _count_unknown(value, _NODE_DIAGNOSTICS_KEYS, d)
    if "ip_addresses" in value:
        addresses = value["ip_addresses"]
        network_type = value.get("network_type")
        if isinstance(network_type, str) and network_type in _IP_NETWORK_TYPES:
            if addresses is None:
                out["ip_addresses"] = None
            else:
                kept = _text_list(addresses, d)
                if kept is not None:
                    out["ip_addresses"] = kept
        elif addresses is not None:
            out["unavailable_fields"] = {"ip_addresses": THREAD_MESH_LOCAL_WITHHELD}
            d.codes.add(THREAD_MESH_LOCAL_WITHHELD)
        else:
            out["ip_addresses"] = None
    if "active_fabrics" in value:
        fabrics = value["active_fabrics"]
        if fabrics is None:
            out["active_fabrics"] = None
        elif not isinstance(fabrics, list):
            d.drop()
        else:
            if len(fabrics) > FABRIC_LIMIT:
                d.drop(len(fabrics) - FABRIC_LIMIT)
            rows = []
            for fabric in fabrics[:FABRIC_LIMIT]:
                if not isinstance(fabric, dict):
                    d.drop()
                    continue
                row: dict[str, Any] = {}
                _pick(fabric, _FABRIC, row, d)
                _count_unknown(fabric, _FABRIC_KEYS, d)
                for name in ("vendor_name", "fabric_label"):
                    if name in row:
                        row[name] = _capped_text(row[name])
                rows.append(row)
            out["active_fabrics"] = rows
    return out


_DEVICE_SCALARS = {
    "device_id": _text_or_none,
    "name": _text_or_none,
    "manufacturer": _text_or_none,
    "model": _text_or_none,
    "sw_version": _text_or_none,
    "hw_version": _text_or_none,
    "serial_number": _text_or_none,
    "area_id": _text_or_none,
    "via_device_id": _text_or_none,
    "friendly_name": _text_or_none,
    "mqtt_topic_hint": _text_or_none,
    "name_by_user": _text_or_none,
    "default_name": _text_or_none,
    "disabled_by": _text_or_none,
}
_DEVICE_IDENTITY = frozenset(
    {"integration_type", "integration_sources", "ieee_address", "zha_trigger_hint", "node_id"}
)
_DEVICE_STRUCTURED = frozenset(
    {
        "labels",
        "config_entries",
        "connections",
        "identifiers",
        "entities",
        "radio_metrics",
        "node_status",
        "node_diagnostics",
    }
)
_DEVICE_KEYS = frozenset(_DEVICE_SCALARS) | _DEVICE_IDENTITY | _DEVICE_STRUCTURED


def _identity(source: dict[str, Any], out: dict[str, Any]) -> int:
    """Copy derived identity fields that pass; return the withheld count."""

    withheld = 0
    if "integration_type" in source:
        value = source["integration_type"]
        if isinstance(value, str) and (
            value in CORE_INTEGRATION_DOMAINS or value in _INTEGRATION_TYPE_EXTRA
        ):
            out["integration_type"] = value
        else:
            withheld += 1
    if "integration_sources" in source:
        value = source["integration_sources"]
        if isinstance(value, list):
            kept = [
                item
                for item in value
                if isinstance(item, str) and item in CORE_INTEGRATION_DOMAINS
            ]
            withheld += len(value) - len(kept)
            out["integration_sources"] = kept
        else:
            withheld += 1
    ieee_passed = False
    if "ieee_address" in source:
        value = source["ieee_address"]
        if isinstance(value, str) and _EUI64.fullmatch(value) is not None:
            out["ieee_address"] = value
            ieee_passed = True
        else:
            withheld += 1
    if "zha_trigger_hint" in source:
        value = source["zha_trigger_hint"]
        if ieee_passed and value == f"Use ieee '{out['ieee_address']}' for zha_event triggers":
            out["zha_trigger_hint"] = value
        else:
            withheld += 1
    if "node_id" in source:
        value = source["node_id"]
        if isinstance(value, str) and _NODE_ID.fullmatch(value) is not None:
            out["node_id"] = value
        else:
            withheld += 1
    return withheld


def _device(source: Any, d: _Disclosure) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        d.drop()
        return None
    out: dict[str, Any] = {}
    _pick(source, _DEVICE_SCALARS, out, d)
    _count_unknown(source, _DEVICE_KEYS, d)
    identity_withheld = _identity(source, out)
    for name in ("labels", "config_entries"):
        if name in source:
            kept = _text_list(source[name], d)
            if kept is not None:
                out[name] = kept
    pairs_withheld = {"connections": 0, "identifiers": 0}
    for name, formats in (("connections", CONNECTION_FORMATS), ("identifiers", IDENTIFIER_FORMATS)):
        if name in source:
            out[name], pairs_withheld[name] = project_registry_pairs(source[name], formats)
    if "entities" in source:
        rows = _entities(source["entities"], d)
        if rows is not None:
            out["entities"] = rows
    for name, spec in (("radio_metrics", _RADIO_METRICS), ("node_status", _NODE_STATUS)):
        if name in source:
            projected = _flat(source[name], spec, d)
            if projected is not None:
                out[name] = projected
    if "node_diagnostics" in source:
        projected = _node_diagnostics(source["node_diagnostics"], d)
        if projected is not None:
            out["node_diagnostics"] = projected
    if any(pairs_withheld.values()):
        d.codes.add(REGISTRY_PAIR_WITHHELD)
        out["registry_pairs_withheld"] = {
            name: _cap(count) for name, count in pairs_withheld.items()
        }
    if identity_withheld:
        d.codes.add(REGISTRY_IDENTITY_WITHHELD)
        out["identity_fields_withheld"] = _cap(identity_withheld)
    return out


def _warning_codes(value: Any, d: _Disclosure) -> None:
    if not isinstance(value, list):
        d.codes.add(ENRICHMENT_WARNING_UNRECOGNIZED)
        return
    for item in value:
        code = ENRICHMENT_WARNING_UNRECOGNIZED
        if isinstance(item, str):
            for prefix, mapped in _WARNING_PREFIXES:
                if item.startswith(prefix):
                    code = mapped
                    break
        d.codes.add(code)


_SINGLE = {
    "entity_count": _is_int,
    "queried_by": lambda v: v in ("device_id", "entity_id"),
    "queried_entity_id": _text_or_none,
}
_SINGLE_KEYS = frozenset(_SINGLE) | {"success", "device", "entities", "warnings"}


def _non_negative_int(value: Any) -> bool:
    return _is_int(value) and value >= 0


_LIST = {
    "total_count": _non_negative_int,
    "offset": _non_negative_int,
    "limit": _non_negative_int,
    "count": _non_negative_int,
    "has_more": lambda v: isinstance(v, bool),
    "next_offset": lambda v: v is None or _non_negative_int(v),
    "total_devices": _non_negative_int,
    "detail_level": lambda v: v in ("summary", "full"),
    "integration_filter": _is_text,
    "area_filter": _is_text,
    "manufacturer_filter": _is_text,
}
_LIST_KEYS = frozenset(_LIST) | {"success", "devices", "filters", "bridge", "usage_hint"}
_BRIDGE = {"device_id": _text_or_none, "name": _text_or_none}
_BRIDGE_KEYS = frozenset(_BRIDGE) | {"ieee_address"}


def _bridge(value: Any, d: _Disclosure) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        d.drop()
        return None
    out: dict[str, Any] = {}
    _pick(value, _BRIDGE, out, d)
    _count_unknown(value, _BRIDGE_KEYS, d)
    if "ieee_address" in value:
        ieee = value["ieee_address"]
        if ieee is None:
            out["ieee_address"] = None
        elif isinstance(ieee, str) and _EUI64.fullmatch(ieee) is not None:
            out["ieee_address"] = ieee
        else:
            d.codes.add(REGISTRY_IDENTITY_WITHHELD)
            out["identity_fields_withheld"] = 1
    return out


def _finish(out: dict[str, Any], d: _Disclosure) -> dict[str, Any]:
    if d.unknown:
        out["fields_withheld"] = _cap(d.unknown)
    if d.codes:
        out["warnings"] = sorted(d.codes)
    return out


def _project_single(payload: dict[str, Any]) -> dict[str, Any]:
    d = _Disclosure()
    device = _device(payload["device"], d)
    if device is None:
        raise ValueError("device_projection_invalid")
    out: dict[str, Any] = {"success": True, "device": device}
    if "entities" in payload:
        rows = _entities(payload["entities"], d)
        if rows is not None:
            out["entities"] = rows
    _pick(payload, _SINGLE, out, d)
    _count_unknown(payload, _SINGLE_KEYS, d)
    if "warnings" in payload:
        _warning_codes(payload["warnings"], d)
    return _finish(out, d)


def _project_list(payload: dict[str, Any]) -> dict[str, Any]:
    d = _Disclosure()
    rows = payload["devices"]
    if not isinstance(rows, list):
        raise ValueError("device_projection_invalid")
    devices = []
    for row in rows:
        projected = _device(row, d)
        if projected is not None:
            devices.append(projected)
    out: dict[str, Any] = {"success": True}
    _pick(payload, _LIST, out, d)
    out["devices"] = devices
    _count_unknown(payload, _LIST_KEYS, d)
    if "filters" in payload:
        kept = _text_list(payload["filters"], d)
        if kept is not None:
            out["filters"] = kept
    if "bridge" in payload:
        bridge = _bridge(payload["bridge"], d)
        if bridge is not None:
            out["bridge"] = bridge
    if "usage_hint" in payload:
        hint = payload["usage_hint"]
        if isinstance(hint, str) and hint in _USAGE_HINTS:
            out["usage_hint"] = hint
        else:
            d.drop()
    return _finish(out, d)


def project_device_result(payload: Any, *, upstream_version: str) -> Any:
    """Return the allowlisted device result; ``ValueError`` fails closed."""

    if upstream_version not in REVIEWED_VERSIONS:
        return payload
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("device_projection_invalid")
    single = "device" in payload
    listed = "devices" in payload
    if single == listed:
        raise ValueError("device_projection_invalid")
    return _project_single(payload) if single else _project_list(payload)
