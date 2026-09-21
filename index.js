'use strict';

const fs = require('fs-extra');
const path = require('path');
const libQ = require('kew');
const { exec } = require('child_process');

const PLUGIN_DIR = '/data/plugins/user_interface/dual_display';
const RUNTIME_CONF = path.join(PLUGIN_DIR, 'dual-display.conf');
const SERVICE = 'dual-display';

module.exports = DualDisplay;

function DualDisplay(context) {
    this.context = context;
    this.commandRouter = this.context.coreCommand;
    this.logger = this.context.logger;
    this.configManager = this.context.configManager;
}

DualDisplay.prototype.onVolumioStart = function () {
    const configFile = this.commandRouter.pluginManager.getConfigurationFile(
        this.context, 'config.json');
    this.config = new (require('v-conf'))();
    this.config.loadFile(configFile);
    return libQ.resolve();
};

DualDisplay.prototype.getConfigurationFiles = function () {
    return ['config.json'];
};

DualDisplay.prototype.onStart = function () {
    const self = this;
    const defer = libQ.defer();

    self.writeRuntimeConfig();

    self.systemctl('start ' + SERVICE)
        .then(function () {
            self.logger.info('Dual Display: started');
            defer.resolve();
        })
        .fail(function (e) {
            self.logger.error('Dual Display: start failed - ' + e);
            defer.reject(e);
        });

    return defer.promise;
};

DualDisplay.prototype.onStop = function () {
    const self = this;
    const defer = libQ.defer();

    self.systemctl('stop ' + SERVICE)
        .then(function () {
            self.logger.info('Dual Display: stopped');
            defer.resolve();
        })
        .fail(function () {
            // The service may already be stopped - that is not an error
            defer.resolve();
        });

    return defer.promise;
};

DualDisplay.prototype.onRestart = function () {
    const self = this;
    self.writeRuntimeConfig();
    return self.systemctl('restart ' + SERVICE);
};

// ------------------------------------------------------------------ settings

DualDisplay.prototype.getUIConfig = function () {
    const self = this;
    const defer = libQ.defer();

    const lang = self.commandRouter.sharedVars.get('language_code');

    self.commandRouter.i18nJson(
        path.join(__dirname, 'i18n', 'strings_' + lang + '.json'),
        path.join(__dirname, 'i18n', 'strings_en.json'),
        path.join(__dirname, 'UIConfig.json'))
        .then(function (uiconf) {
            // Without projectMSDL there is no visualizer - on ARM, for
            // one. Visualizer and Audio settings would do nothing there,
            // so only the display settings are shown.
            if (!fs.existsSync('/usr/bin/projectMSDL')) {
                uiconf.sections = uiconf.sections.filter(function (section) {
                    return section.id === 'section_displays';
                });
            }

            // Fill values by field id rather than by position, so adding
            // a field does not shift everything after it
            uiconf.sections.forEach(function (section) {
                section.content.forEach(function (field) {
                    const v = self.config.get(field.id);
                    if (v !== undefined) {
                        field.value = v;
                    }
                });
            });

            defer.resolve(uiconf);
        })
        .fail(function (e) {
            self.logger.error('Dual Display: getUIConfig failed - ' + e);
            defer.reject(new Error());
        });

    return defer.promise;
};

DualDisplay.prototype.saveScreens = function (data) {
    const self = this;

    self.config.set('primary_output', data['primary_output']);
    self.config.set('secondary_output', data['secondary_output']);

    return self.applyAndRestart();
};

DualDisplay.prototype.saveVisualizer = function (data) {
    const self = this;

    self.config.set('double_tap_time', data['double_tap_time']);
    self.config.set('tap_zone_x2', data['tap_zone_x2']);
    self.config.set('auto_change', data['auto_change']);
    self.config.set('preset_duration', data['preset_duration']);
    self.config.set('transition_duration', data['transition_duration']);
    self.config.set('beat_sensitivity', data['beat_sensitivity']);
    self.config.set('fps', data['fps']);

    return self.applyAndRestart();
};

DualDisplay.prototype.saveAudio = function (data) {
    const self = this;

    self.config.set('loopback_out', data['loopback_out']);
    self.config.set('audio_device', data['audio_device']);

    return self.applyAndRestart();
};

DualDisplay.prototype.applyAndRestart = function () {
    const self = this;
    const defer = libQ.defer();

    self.writeRuntimeConfig();

    self.systemctl('restart ' + SERVICE)
        .then(function () {
            self.commandRouter.pushToastMessage(
                'success',
                self.getI18n('DD_PLUGIN_NAME'),
                self.getI18n('DD_SETTINGS_SAVED'));
            defer.resolve({});
        })
        .fail(function (e) {
            self.commandRouter.pushToastMessage(
                'error',
                self.getI18n('DD_PLUGIN_NAME'),
                String(e));
            defer.reject(e);
        });

    return defer.promise;
};

// ----------------------------------------------------------------- helpers

/**
 * Plugin settings live in config.json, while the Python controller reads
 * a plain text dual-display.conf. This turns one into the other.
 */
DualDisplay.prototype.writeRuntimeConfig = function () {
    const self = this;

    const lines = [
        '# Written by the plugin on every start.',
        '# Edits here are overwritten - change the settings in Volumio.',
        '',
        'TOUCH_DEV="' + self.config.get('touch_dev', '') + '"',
        'DOUBLE_TAP_TIME=' + self.config.get('double_tap_time', 0.3),
        '',
        'PRIMARY_OUTPUT="' + self.config.get('primary_output', '') + '"',
        'SECONDARY_OUTPUT="' + self.config.get('secondary_output', '') + '"',
        'SECOND_SCREEN_URL="' + self.config.get('second_screen_url',
            'http://localhost:3000') + '"',
        'CHROMIUM_PROFILE="' + self.config.get('chromium_profile',
            '/data/volumiokiosk-tv') + '"',
        '',
        'LOOPBACK_OUT="' + self.config.get('loopback_out', 'plughw:1,0') + '"',
        'AUDIO_DEVICE=' + self.config.get('audio_device', 2),
        'VOLUMIO_FIFO="' + self.config.get('volumio_fifo',
            '/tmp/stream.mp3') + '"',
        '',
        'PROJECTM="/usr/bin/projectMSDL"',
        'NEXT_KEY=' + self.config.get('next_key', 'n'),
        '',
        'TAP_ZONE_X1=' + self.config.get('tap_zone_x1', 0.0),
        'TAP_ZONE_X2=' + self.config.get('tap_zone_x2', 0.33),
        'TAP_ZONE_Y1=' + self.config.get('tap_zone_y1', 0.0),
        'TAP_ZONE_Y2=' + self.config.get('tap_zone_y2', 1.0),
        '',
        'AUTO_CHANGE=' + (self.config.get('auto_change', false) ? 'true' : 'false'),
        'PRESET_DURATION=' + self.config.get('preset_duration', 30),
        'TRANSITION_DURATION=' + self.config.get('transition_duration', 3),
        'BEAT_SENSITIVITY=' + self.config.get('beat_sensitivity', 1),
        'FPS=' + self.config.get('fps', 60),
        '',
        'POLL_INTERVAL=' + self.config.get('poll_interval', 2.0),
        ''
    ];

    try {
        fs.writeFileSync(RUNTIME_CONF, lines.join('\n'));
    } catch (e) {
        self.logger.error('Dual Display: cannot write conf - ' + e);
    }
};

DualDisplay.prototype.systemctl = function (command) {
    const self = this;
    const defer = libQ.defer();

    exec('/usr/bin/sudo /bin/systemctl ' + command,
        { uid: 1000, gid: 1000 },
        function (error) {
            if (error) {
                self.logger.error('Dual Display: systemctl ' +
                    command + ' failed - ' + error);
                defer.reject(error);
            } else {
                defer.resolve();
            }
        });

    return defer.promise;
};

DualDisplay.prototype.getI18n = function (key) {
    const self = this;
    if (self.i18nStrings && self.i18nStrings[key]) {
        return self.i18nStrings[key];
    }
    try {
        self.i18nStrings = fs.readJsonSync(
            path.join(__dirname, 'i18n', 'strings_en.json'));
        return self.i18nStrings[key] || key;
    } catch (e) {
        return key;
    }
};
