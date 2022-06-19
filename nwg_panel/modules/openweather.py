#!/usr/bin/env python3

import json
import os
import stat
import sys
import subprocess
import threading
from datetime import datetime

import gi

try:
    import requests
except ModuleNotFoundError:
    print("You need to install python-requests package", file=sys.stderr)
    sys.exit(1)

from nwg_panel.tools import check_key, eprint, load_json, save_json, temp_dir, file_age, hms, update_image, \
    get_config_dir

config_dir = get_config_dir()
dir_name = os.path.dirname(__file__)

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')

from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, GtkLayerShell


def on_enter_notify_event(widget, event):
    widget.set_state_flags(Gtk.StateFlags.DROP_ACTIVE, clear=False)
    widget.set_state_flags(Gtk.StateFlags.SELECTED, clear=False)


def on_leave_notify_event(widget, event):
    widget.unset_state_flags(Gtk.StateFlags.DROP_ACTIVE)
    widget.unset_state_flags(Gtk.StateFlags.SELECTED)


degrees = {"standard": "°K", "metric": "°C", "imperial": "°F"}


def direction(deg):
    if 0 <= deg <= 23 or 337 <= deg <= 360:
        return "N"
    elif 24 <= deg <= 68:
        return "NE"
    elif 69 <= deg <= 113:
        return "E"
    elif 114 <= deg <= 158:
        return "SE"
    elif 159 <= deg <= 203:
        return "S"
    elif 204 <= deg <= 248:
        return "SW"
    elif 249 <= deg <= 293:
        return "W"
    elif 293 <= deg <= 336:
        return "NW"
    else:
        return "WTF"


def on_button_press(window, event):
    window.close()
    window.destroy()


class OpenWeather(Gtk.EventBox):
    def __init__(self, settings, icons_path=""):
        defaults = {"appid": "",
                    "lat": None,
                    "long": None,
                    "lang": "en",
                    "units": "metric",
                    "interval": 1800,
                    "loc-name": "",
                    "weather-icons": "color",

                    "on-right-click": "",
                    "on-middle-click": "",
                    "on-scroll": "",
                    "icon-placement": "start",
                    "icon-size": 24,
                    "css-name": "weather",
                    "show-name": False,
                    "angle": 0.0,

                    "ow-popup-icons": "light",
                    "popup-header-icon-size": 48,
                    "popup-icon-size": 24,
                    "popup-text-size": "medium",
                    "popup-css-name": "weather-forecast",
                    "popup-placement": "right",
                    "popup-margin-horizontal": 0,
                    "popup-margin-top": 0,
                    "popup-margin-bottom": 0,
                    "show-humidity": True,
                    "show-wind": True,
                    "show-pressure": True,
                    "show-cloudiness": True,
                    "show-visibility": True,
                    "show-pop": True,
                    "show-volume": True,
                    "module-id": ""}

        Gtk.EventBox.__init__(self)

        for key in defaults:
            check_key(settings, key, defaults[key])

        self.set_property("name", settings["css-name"])

        self.settings = settings

        self.lang = load_json(os.path.join(config_dir, "langs/weather_en"))
        if self.settings["lang"]:
            loc_file = os.path.join(config_dir, "langs/weather_{}".format(self.settings["lang"]))
            try:
                loc = load_json(loc_file)
                for key in loc:
                    self.lang[key] = loc[key]
                eprint("Loaded translation from {}".format(loc_file))
            except:
                eprint("Translation into '{}' corrupted or does not exist.".format(self.settings["lang"]))

        self.icons_path = icons_path
        self.popup_icons = os.path.join(config_dir, "icons_light") if self.settings[
                                                                          "ow-popup-icons"] == "light" else os.path.join(
            config_dir, "icons_dark")

        if self.settings["weather-icons"] == "light":
            self.weather_icons = os.path.join(config_dir, "icons_light")
        elif self.settings["weather-icons"] == "dark":
            self.weather_icons = os.path.join(config_dir, "icons_dark")
        else:
            self.weather_icons = os.path.join(config_dir, "icons_color")

        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add(self.box)
        self.image = Gtk.Image()
        self.label = Gtk.Label.new("No weather data")
        self.icon_path = None

        self.weather = None
        self.forecast = None

        self.connect('button-press-event', self.on_button_press)
        self.add_events(Gdk.EventMask.SCROLL_MASK)
        self.connect('scroll-event', self.on_scroll)

        self.connect('enter-notify-event', on_enter_notify_event)
        self.connect('leave-notify-event', on_leave_notify_event)

        self.popup = Gtk.Window()

        if settings["angle"] != 0.0:
            self.box.set_orientation(Gtk.Orientation.VERTICAL)
            self.label.set_angle(settings["angle"])

        update_image(self.image, "view-refresh-symbolic", self.settings["icon-size"], self.icons_path)

        data_home = os.getenv('XDG_DATA_HOME') if os.getenv('XDG_DATA_HOME') else os.path.join(os.getenv("HOME"),
                                                                                               ".local/share")
        tmp_dir = temp_dir()
        self.weather_file = "{}-{}".format(os.path.join(tmp_dir, "nwg-openweather-weather"), settings["module-id"])
        self.forecast_file = "{}-{}".format(os.path.join(tmp_dir, "nwg-openweather-forecast"), settings["module-id"])
        eprint("Weather file: {}".format(self.weather_file))
        eprint("Forecast file: {}".format(self.forecast_file))

        # Try to obtain geolocation if unset
        if not settings["lat"] or not settings["long"]:
            # Try nwg-shell settings
            shell_settings_file = os.path.join(data_home, "nwg-shell-config", "settings")
            if os.path.isfile(shell_settings_file):
                shell_settings = load_json(shell_settings_file)
                eprint("OpenWeather: coordinates not set, loading from nwg-shell settings")
                settings["lat"] = shell_settings["night-lat"]
                settings["long"] = shell_settings["night-long"]
                eprint("lat = {}, long = {}".format(settings["lat"], settings["long"]))
            else:
                # Set dummy location
                eprint("OpenWeather: coordinates not set, setting Big Ben in London 51.5008, -0.1246")
                settings["lat"] = 51.5008
                settings["long"] = -0.1246

        self.weather_request = "https://api.openweathermap.org/data/2.5/weather?lat={}&lon={}&units={}&lang={}&appid={}".format(
            settings["lat"], settings["long"], settings["units"], settings["lang"], settings["appid"])

        self.forecast_request = "https://api.openweathermap.org/data/2.5/forecast?lat={}&lon={}&units={}&lang={}&appid={}".format(
            settings["lat"], settings["long"], settings["units"], settings["lang"], settings["appid"])

        self.build_box()

        self.refresh()

        if settings["interval"] > 0:
            # We can't use `self.settings["interval"]` here, as the timer resets on restart. Let's check once a minute.
            # This will do nothing if files exist and self.weather & self.forecast are not None.
            Gdk.threads_add_timeout_seconds(GLib.PRIORITY_DEFAULT, 60, self.refresh)

    def build_box(self):
        if self.settings["icon-placement"] == "start":
            self.box.pack_start(self.image, False, False, 2)
        self.box.pack_start(self.label, False, False, 2)

        if self.settings["icon-placement"] != "start":
            self.box.pack_start(self.image, False, False, 2)

    def get_data(self):
        self.get_weather()
        self.get_forecast()
        GLib.idle_add(self.update_widget)
        return True

    def refresh(self):
        thread = threading.Thread(target=self.get_data)
        thread.daemon = True
        thread.start()
        return True

    def on_button_press(self, widget, event):
        if event.button == 1:
            self.display_popup()
        elif event.button == 2 and self.settings["on-middle-click"]:
            self.launch(self.settings["on-middle-click"])
        elif event.button == 3 and self.settings["on-right-click"]:
            self.launch(self.settings["on-right-click"])

    def on_scroll(self, widget, event):
        if event.direction == Gdk.ScrollDirection.UP and self.settings["on-scroll"]:
            self.launch(self.settings["on-scroll"])
        elif event.direction == Gdk.ScrollDirection.DOWN and self.settings["on-scroll"]:
            self.launch(self.settings["on-scroll"])
        else:
            print("No command assigned")

    def launch(self, cmd):
        print("Executing '{}'".format(cmd))
        subprocess.Popen('exec {}'.format(cmd), shell=True)

    def get_weather(self):
        if not os.path.isfile(self.weather_file) or int(file_age(self.weather_file) > self.settings["interval"] - 1):
            eprint(hms(), "Requesting weather data")
            try:
                r = requests.get(self.weather_request)
                self.weather = json.loads(r.text)
                if self.weather["cod"] in ["200", 200]:
                    save_json(self.weather, self.weather_file)
            except Exception as e:
                self.weather = None
                eprint(e)
        elif not self.weather:
            eprint(hms(), "Loading weather data from file")
            self.weather = load_json(self.weather_file)

    def get_forecast(self):
        if not os.path.isfile(self.forecast_file) or int(file_age(self.forecast_file) > self.settings["interval"] - 1):
            eprint(hms(), "Requesting forecast data")
            try:
                r = requests.get(self.forecast_request)
                self.forecast = json.loads(r.text)
                if self.forecast["cod"] in ["200", 200]:
                    save_json(self.forecast, self.forecast_file)
            except Exception as e:
                self.forecast = None
                eprint(e)
        elif not self.forecast:
            eprint(hms(), "Loading forecast data from file")
            self.forecast = load_json(self.forecast_file)

    def update_widget(self):
        if self.weather and self.weather["cod"] and self.weather["cod"] in [200, "200"]:
            if "icon" in self.weather["weather"][0]:
                new_path = os.path.join(self.weather_icons, "ow-{}.svg".format(self.weather["weather"][0]["icon"]))
                if self.icon_path != new_path:
                    try:
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(
                            new_path, self.settings["icon-size"], self.settings["icon-size"])
                        self.image.set_from_pixbuf(pixbuf)
                        self.icon_path = new_path
                    except:
                        print("Failed setting image from {}".format(new_path))
            lbl_content = ""
            if "name" in self.weather:
                desc = self.weather["name"] if not self.settings["loc-name"] else self.settings["loc-name"]
                if self.settings["show-name"]:
                    lbl_content += "{} ".format(desc)

            if "temp" in self.weather["main"] and self.weather["main"]["temp"]:
                deg = degrees[self.settings["units"]]
                try:
                    val = round(float(self.weather["main"]["temp"]), 1)
                    temp = "{}{}".format(str(val), deg)
                    lbl_content += temp
                except:
                    pass

            self.label.set_text(lbl_content)

            mtime = datetime.fromtimestamp(os.stat(self.weather_file)[stat.ST_MTIME])
            self.set_tooltip_text("Update: {}".format(mtime.strftime("%d %b %H:%M:%S")))

        self.show_all()

    def svg2img(self, file_name, weather=False):
        icon_path = os.path.join(self.popup_icons, file_name) if not weather else os.path.join(self.weather_icons,
                                                                                               file_name)
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, self.settings["popup-icon-size"],
                                                            self.settings["popup-icon-size"])
            img = Gtk.Image.new_from_pixbuf(pixbuf)
        except Exception as e:
            eprint(e)
            img = Gtk.Image.new_from_icon_name("image-missing", Gtk.IconSize.MENU)
            img.set_tooltip_text(str(e))

        return img

    def display_popup(self):
        if not self.weather or not self.weather["cod"] in ["200", 200] or not self.forecast["cod"] in ["200", 200]:
            print("No data available")
            return

        if self.popup.is_visible():
            self.popup.close()
            self.popup.destroy()
            return

        self.popup.destroy()

        self.popup = Gtk.Window.new(Gtk.WindowType.TOPLEVEL)
        self.popup.set_property("name", self.settings["popup-css-name"])

        GtkLayerShell.init_for_window(self.popup)

        GtkLayerShell.set_layer(self.popup, GtkLayerShell.Layer.TOP)

        # stretch vertically to the entire window height
        GtkLayerShell.set_anchor(self.popup, GtkLayerShell.Edge.TOP, 1)
        GtkLayerShell.set_anchor(self.popup, GtkLayerShell.Edge.BOTTOM, 1)

        # attach to left/right margin or just center
        if self.settings["popup-placement"] == "left":
            GtkLayerShell.set_anchor(self.popup, GtkLayerShell.Edge.LEFT, 1)
        elif self.settings["popup-placement"] == "right":
            GtkLayerShell.set_anchor(self.popup, GtkLayerShell.Edge.RIGHT, 1)

        # set vertical margin (same for top & bottom)
        GtkLayerShell.set_margin(self.popup, GtkLayerShell.Edge.TOP, self.settings["popup-margin-top"])
        GtkLayerShell.set_margin(self.popup, GtkLayerShell.Edge.BOTTOM, self.settings["popup-margin-bottom"])

        # set horizontal margin (same for left & right)
        GtkLayerShell.set_margin(self.popup, GtkLayerShell.Edge.LEFT, self.settings["popup-margin-horizontal"])
        GtkLayerShell.set_margin(self.popup, GtkLayerShell.Edge.RIGHT, self.settings["popup-margin-horizontal"])

        self.popup.connect('button-release-event', on_button_press)

        vbox = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        vbox.set_property("margin", 6)
        self.popup.add(vbox)

        # CURRENT WEATHER
        # row 0: Big icon
        hbox = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
        if "icon" in self.weather["weather"][0]:
            icon_path = os.path.join(self.weather_icons, "ow-{}.svg".format(self.weather["weather"][0]["icon"]))
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, self.settings["popup-header-icon-size"],
                                                            self.settings["popup-header-icon-size"])
            img = Gtk.Image.new_from_pixbuf(pixbuf)
            img.set_property("halign", Gtk.Align.END)
            hbox.pack_start(img, True, True, 0)

        if "description" in self.weather["weather"][0]:
            hbox.set_tooltip_text(self.weather["weather"][0]["description"])

        # row 0: Temperature big label
        if "temp" in self.weather["main"]:
            lbl = Gtk.Label()
            temp = self.weather["main"]["temp"]
            lbl.set_markup(
                '<span size="xx-large">{}{}</span>'.format(str(round(temp, 1)), degrees[self.settings["units"]]))
            lbl.set_property("halign", Gtk.Align.START)
            hbox.pack_start(lbl, True, True, 0)

        vbox.pack_start(hbox, False, False, 0)

        # row 1: Location
        hbox = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        loc_label = self.weather["name"] if "name" in self.weather and not self.settings["loc-name"] else \
            self.settings["loc-name"]
        country = ", {}".format(self.weather["sys"]["country"]) if "country" in self.weather["sys"] and \
                                                                   self.weather["sys"][
                                                                       "country"] else ""
        hbox.set_tooltip_text("{}, {}".format(self.settings["lat"], self.settings["long"]))
        lbl = Gtk.Label()
        lbl.set_markup('<span size="x-large">{}{}</span>'.format(loc_label, country))
        hbox.pack_start(lbl, True, True, 0)
        vbox.pack_start(hbox, False, False, 0)

        # row 2: Sunrise/sunset
        if self.weather["sys"]["sunrise"] and self.weather["sys"]["sunset"]:
            wbox = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
            vbox.pack_start(wbox, False, False, 0)
            hbox = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
            wbox.pack_start(hbox, True, False, 0)
            img = self.svg2img("sunrise.svg")
            hbox.pack_start(img, False, False, 0)
            dt = datetime.fromtimestamp(self.weather["sys"]["sunrise"])
            lbl = Gtk.Label.new(dt.strftime("%H:%M"))
            hbox.pack_start(lbl, False, False, 0)
            img = self.svg2img("sunset.svg")
            hbox.pack_start(img, False, False, 0)
            dt = datetime.fromtimestamp(self.weather["sys"]["sunset"])
            lbl = Gtk.Label.new(dt.strftime("%H:%M"))
            hbox.pack_start(lbl, False, False, 0)

        # row 3: Weather details
        hbox = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        lbl = Gtk.Label()
        lbl.set_property("justify", Gtk.Justification.CENTER)
        feels_like = "{}: {}°".format(self.lang["feels-like"], self.weather["main"]["feels_like"]) if "feels_like" in \
                                                                                                      self.weather[
                                                                                                          "main"] else ""
        humidity = "   {}: {}%".format(self.lang["humidity"], self.weather["main"]["humidity"]) if "humidity" in \
                                                                                                   self.weather[
                                                                                                       "main"] else ""
        wind_speed, wind_dir, wind_gust = "", "", ""
        if "wind" in self.weather:
            if "speed" in self.weather["wind"]:
                wind_speed = "   {}: {} m/s".format(self.lang["wind"], self.weather["wind"]["speed"])
            if "deg" in self.weather["wind"]:
                wind_dir = " {}".format((direction(self.weather["wind"]["deg"])))
            if "gust" in self.weather["wind"]:
                wind_gust = " ({} {} m/s)".format(self.lang["gust"], self.weather["wind"]["gust"])
        pressure = " {}: {} hPa".format(self.lang["pressure"], self.weather["main"]["pressure"]) if "pressure" in \
                                                                                                    self.weather[
                                                                                                        "main"] else ""
        clouds = "   {}: {}%".format(self.lang["cloudiness"],
                                     self.weather["clouds"]["all"]) if "clouds" in self.weather and "all" in \
                                                                       self.weather["clouds"] else ""
        visibility = "   {}: {} km".format(self.lang["visibility"], int(
            self.weather["visibility"] / 1000)) if "visibility" in self.weather else ""
        lbl.set_markup(
            '<span font_size="{}">{}{}{}{}{}\n{}{}{}</span>'.format(self.settings["popup-text-size"], feels_like,
                                                                    humidity,
                                                                    wind_speed, wind_dir, wind_gust, pressure, clouds,
                                                                    visibility))
        hbox.pack_start(lbl, True, True, 0)
        vbox.pack_start(hbox, False, False, 6)

        # 5-DAY FORECAST
        if self.forecast["cod"] in [200, "200"]:
            lbl = Gtk.Label()
            lbl.set_markup(
                '<span font_size="{}"><big>{}</big></span>'.format(self.settings["popup-text-size"],
                                                                   self.lang["5-day-forecast"]))
            vbox.pack_start(lbl, False, False, 6)

            scrolled_window = Gtk.ScrolledWindow.new(None, None)
            scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

            grid = Gtk.Grid.new()
            grid.set_column_spacing(3)
            grid.set_row_spacing(3)

            scrolled_window.add_with_viewport(grid)
            vbox.pack_start(scrolled_window, True, True, 0)

            for i in range(len(self.forecast["list"])):
                data = self.forecast["list"][i]

                # Bullet
                img = self.svg2img("pan-end-symbolic.svg")
                grid.attach(img, 0, i, 1, 1)

                # Date
                dt = datetime.fromtimestamp(data["dt"]).strftime("%a, %d %b")
                box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
                lbl = Gtk.Label()
                lbl.set_markup('<span font_size="{}">{}</span>'.format(self.settings["popup-text-size"], dt))
                box.pack_start(lbl, False, False, 0)
                grid.attach(box, 1, i, 1, 1)

                # Time
                dt = datetime.fromtimestamp(data["dt"]).strftime("<b>%H:%M</b>")
                box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
                lbl = Gtk.Label()
                lbl.set_markup(
                    '<span font_size="{}"><tt>{}</tt></span>'.format(self.settings["popup-text-size"], dt))
                box.pack_start(lbl, False, False, 0)
                grid.attach(box, 2, i, 1, 1)

                # Icon
                if "weather" in data and data["weather"][0]:
                    if "icon" in data["weather"][0]:
                        img = self.svg2img("ow-{}.svg".format(data["weather"][0]["icon"]), weather=True)
                        img.set_property("margin-start", 6)
                        img.set_property("margin-end", 2)
                        grid.attach(img, 3, i, 1, 1)
                    if "description" in data["weather"][0]:
                        img.set_tooltip_text(data["weather"][0]["description"])

                # Temperature
                if "temp" in data["main"] and data["main"]["temp"]:
                    lbl = Gtk.Label()
                    feels_like = ""
                    if "feels_like" in data["main"] and data["main"]["feels_like"]:
                        feels_like = " ({}°)".format(int(round(data["main"]["feels_like"], 0)))
                    lbl.set_markup('<span font_size="{}">{}°{}</span>'.format(self.settings["popup-text-size"],
                                                                              str(int(round(data["main"]["temp"], 0))),
                                                                              feels_like))
                    grid.attach(lbl, 4, i, 1, 1)

                # Humidity
                if self.settings["show-humidity"] and "humidity" in data["main"] and data["main"]["humidity"]:
                    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
                    img = self.svg2img("humidity.svg")
                    box.pack_start(img, False, False, 0)
                    lbl = Gtk.Label()
                    lbl.set_markup('<span font_size="{}">{}%</span>'.format(self.settings["popup-text-size"],
                                                                            data["main"]["humidity"]))
                    box.pack_start(lbl, False, False, 0)
                    grid.attach(box, 5, i, 1, 1)

                # Wind
                if self.settings["show-wind"] and "wind" in data and data["wind"]:
                    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
                    img = self.svg2img("wind.svg")
                    box.pack_start(img, False, False, 0)
                    wind_speed = "{} m/s".format(data["wind"]["speed"]) if "speed" in data["wind"] and data["wind"][
                        "speed"] else ""
                    wind_gust = " ({})".format(data["wind"]["gust"]) if "gust" in data["wind"] and data["wind"][
                        "gust"] else ""
                    wind_dir = " {}".format(direction(data["wind"]["deg"])) if "deg" in data["wind"] and data["wind"][
                        "deg"] else ""
                    lbl = Gtk.Label()
                    lbl.set_markup(
                        '<span font_size="{}">{}{}{}</span>'.format(self.settings["popup-text-size"], wind_speed,
                                                                    wind_gust, wind_dir))
                    box.pack_start(lbl, False, False, 0)
                    grid.attach(box, 6, i, 1, 1)

                # Pressure
                if self.settings["show-pressure"] and "pressure" in data["main"] and data["main"]["pressure"]:
                    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
                    img = self.svg2img("pressure.svg")
                    box.pack_start(img, False, False, 0)
                    lbl = Gtk.Label()
                    lbl.set_markup('<span font_size="{}">{} hPa</span>'.format(self.settings["popup-text-size"],
                                                                               data["main"]["pressure"]))
                    box.pack_start(lbl, False, False, 0)
                    grid.attach(box, 7, i, 1, 1)

                # Cloudiness
                if self.settings["show-cloudiness"] and "clouds" in data:
                    if "all" in data["clouds"]:
                        box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
                        img = self.svg2img("cloud.svg")
                        box.pack_start(img, False, False, 0)
                        lbl = Gtk.Label()
                        lbl.set_markup('<span font_size="{}">{}%</span>'.format(self.settings["popup-text-size"],
                                                                                data["clouds"]["all"]))
                        box.pack_start(lbl, False, False, 0)
                        grid.attach(box, 8, i, 1, 1)

                # Visibility
                if self.settings["show-visibility"] and "visibility" in data:
                    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
                    img = self.svg2img("eye.svg")
                    box.pack_start(img, False, False, 0)
                    lbl = Gtk.Label()
                    lbl.set_markup('<span font_size="{}">{} km</span>'.format(self.settings["popup-text-size"],
                                                                              int(data["visibility"] / 1000)))
                    box.pack_start(lbl, False, False, 0)
                    grid.attach(box, 9, i, 1, 1)

                # Probability of precipitation
                if self.settings["show-pop"] and "pop" in data and data["pop"]:
                    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
                    img = self.svg2img("umbrella.svg")
                    box.pack_start(img, False, False, 0)
                    lbl = Gtk.Label()
                    lbl.set_markup('<span font_size="{}">{}%</span>'.format(self.settings["popup-text-size"],
                                                                            int(round(data["pop"] * 100, 0))))
                    box.pack_start(lbl, False, False, 0)
                    grid.attach(box, 10, i, 1, 1)

                # Precipitation volume
                if self.settings["show-volume"]:
                    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
                    img = self.svg2img("measure.svg")
                    box.pack_start(img, False, False, 0)
                    if "rain" in data and "3h" in data["rain"]:
                        lbl = Gtk.Label()
                        lbl.set_markup('<span font_size="{}">{} mm</span>'.format(self.settings["popup-text-size"],
                                                                                  round(data["rain"]["3h"], 2)))
                        box.pack_start(lbl, False, False, 0)
                        grid.attach(box, 11, i, 1, 1)

                    if "snow" in data and "3h" in data["snow"]:
                        lbl = Gtk.Label()
                        lbl.set_markup('<span font_size="{}">{} mm</span>'.format(self.settings["popup-text-size"],
                                                                                  round(data["rain"]["3h"], 2)))
                        box.pack_start(lbl, False, False, 0)
                        grid.attach(box, 12, i, 1, 1)

            if os.path.isfile(self.forecast_file):
                sep = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
                vbox.pack_start(sep, False, False, 0)
                mtime = datetime.fromtimestamp(os.stat(self.forecast_file)[stat.ST_MTIME])
                lbl = Gtk.Label()
                lbl.set_markup(
                    '<span font_size="{}">openweathermap.org, {}</span>'.format(self.settings["popup-text-size"],
                                                                                mtime.strftime("%d %B %H:%M:%S")))
                lbl.set_property("margin-top", 3)
            vbox.pack_start(lbl, False, False, 0)

        self.popup.show_all()