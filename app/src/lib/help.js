/**
 * The help center's content: every topic the Help sheet can show.
 *
 * Data, not markup. Each topic is a list of blocks the sheet renders with
 * ordinary elements, so nothing here goes through {@html} and a typo in a
 * sentence cannot break the page. Inline emphasis is the one exception to
 * "plain text": **double asterisks** mark a word as strong, and that is all
 * the formatting there is.
 *
 * The facts come from the firmware, not from memory: the defaults are
 * config.py's, the lights are drive_outputs(), the button is
 * handle_button(), the fault texts are check_fan_health(). When one of
 * those changes, the topic that quotes it changes with it. Numbers that a
 * person can change in Thresholds are given as "by default", because the
 * fan on the wall may well have been tuned.
 *
 * A topic's go: [...] lists the PLACES it talks about. The sheet shows
 * them as "Open in the app" buttons under the summary, so a topic that says
 * "on the Health screen" takes you there.
 *
 * Block kinds:
 *   { p: 'text' }                    a paragraph
 *   { steps: ['…', '…'] }            numbered steps
 *   { list: ['…', '…'] }             bullets
 *   { rows: [['term', 'meaning']] }  a two-column definition list
 *   { note: 'text' }                 a callout for the thing people get wrong
 *   { see: ['topic-id', …] }         links to related topics
 *   { links: [{ href, label, note }] } links out of the app, opened in a
 *                                    new tab; only to the hosts in
 *                                    LINK_HOSTS (tests/test_help.mjs)
 */

/**
 * Where the project lives. One place, because the Home Assistant topics
 * build several links from it, and test_help.mjs checks it against the
 * integration's own manifest.json so a renamed repo cannot leave the help
 * pointing at the old one.
 */
export const REPO_OWNER = 'deokman420';
export const REPO_NAME = 'smartfan';
export const REPO_URL = `https://github.com/${REPO_OWNER}/${REPO_NAME}`;

/** The only places help links out to. */
export const LINK_HOSTS = ['github.com', 'my.home-assistant.io', 'hacs.xyz'];

/**
 * Places in the app a topic can send you to.
 *
 * view is the tab (the page's VIEWS ids); sheet: 'settings' opens the
 * Settings sheet instead. anchor is the id of the element to scroll to and
 * highlight; tests/test_help.mjs checks that every anchor here really exists
 * in the page or WifiPanel source, so renaming one cannot quietly turn a
 * link into a jump to the top of the wrong screen.
 */
export const PLACES = {
  status: { view: 'status', label: 'Status' },
  'status-control': { view: 'status', anchor: 'place-control', label: 'Status › Control' },
  readings: { view: 'readings', label: 'Readings' },
  charts: { view: 'charts', label: 'Charts' },
  'tune-humidity': { view: 'tuning', anchor: 'place-tune-humidity', label: 'Thresholds › Humidity' },
  'tune-light': { view: 'tuning', anchor: 'place-tune-light', label: 'Thresholds › Light' },
  'tune-odor': { view: 'tuning', anchor: 'place-tune-odor', label: 'Thresholds › Odor' },
  'tune-timing': { view: 'tuning', anchor: 'place-tune-timing', label: 'Thresholds › Timing' },
  'tune-mode': { view: 'tuning', anchor: 'place-tune-mode', label: 'Thresholds › Mode and reset' },
  'health-link': { view: 'health', anchor: 'place-health-link', label: 'Health › Link' },
  'health-device': { view: 'health', anchor: 'place-health-device', label: 'Health › Device' },
  'settings-connection': { sheet: 'settings', anchor: 'place-connection', label: 'Settings › Connection' },
  'settings-network': { sheet: 'settings', anchor: 'place-network', label: 'Settings › Network' },
  'settings-broker': { sheet: 'settings', anchor: 'place-broker', label: 'Settings › Broker' }
};

export const SECTIONS = [
  { id: 'start', title: 'Getting started' },
  { id: 'use', title: 'Everyday use' },
  { id: 'tune', title: 'Thresholds' },
  { id: 'setup', title: 'Setup and connections' },
  { id: 'fix', title: 'Troubleshooting' }
];

export const TOPICS = [
  // --- Getting started ------------------------------------------------
  {
    id: 'what',
    section: 'start',
    title: 'What Smartfan does',
    summary: 'It runs the fan by itself, and keeps working without the app or Wi-Fi.',
    keywords: 'about overview automatic offline how it works',
    blocks: [
      {
        p: 'Smartfan switches the bathroom fan on and off for you. It watches three things: the **humidity**, the **light**, and (if the odor sensor is fitted) the **air quality**. When any of them says the room needs clearing, the fan runs; when the room is back to normal, it stops.'
      },
      {
        p: 'It judges each one against what is normal for your room, not against a fixed number. It learns that normal by itself, which is why a new fan spends its first minute learning.'
      },
      {
        note: 'All of the decisions are made on the fan itself. If the Wi-Fi drops, Home Assistant is down, or this app is closed, the fan carries on exactly the same. The app only lets you watch and adjust it.'
      },
      { see: ['status', 'states', 'lights'] }
    ]
  },
  {
    id: 'status',
    section: 'start',
    title: 'Reading the Status screen',
    summary: 'The big gauge, the state, and what the fan is doing right now.',
    keywords: 'gauge hero dashboard home screen above baseline excess',
    go: ['status'],
    blocks: [
      {
        p: 'The gauge shows how far the humidity is **above the room’s normal level** (its baseline), not the humidity itself. A bathroom at 55 % that normally sits at 50 % reads +5 %.'
      },
      {
        rows: [
          ['Green band', 'Normal. The fan has no humidity reason to run.'],
          ['Amber band', 'Between the stop and start lines. A running fan keeps going here; an idle one stays off.'],
          ['Red band', 'Past the start line. The fan runs.']
        ]
      },
      {
        p: 'Next to the gauge, **Controller** shows the state (IDLE, RUN and so on) and a line saying why, such as “Clearing humidity” or “Running with the light”.'
      },
      {
        p: 'The colored pill at the top says whether the app can hear the fan: **Live** is good, **No update** means readings have stopped arriving, and **Fan offline** means the fan is not answering.'
      },
      { see: ['states', 'readings', 'fix-offline'] }
    ]
  },
  {
    id: 'lights',
    section: 'start',
    title: 'The lights on the box',
    summary: 'What the green and red lights mean.',
    keywords: 'led leds blink blinking flashing green red alternating indicator',
    go: ['health-device'],
    blocks: [
      { p: 'The box has a green light and a red one. Together they say what the fan is doing:' },
      {
        rows: [
          ['Green, blinking evenly', 'Learning the room’s normal levels (the first minute after power-on, or after a relearn).'],
          ['Green, one short flash every few seconds', 'Idle. The odor sensor is still warming up; light and humidity already work.'],
          ['Green, steady', 'Idle and watching. The fan is off.'],
          ['Red, steady', 'The fan is running.'],
          ['Red, blinking', 'Boost is on.'],
          ['Green and red taking turns', 'A fault. Something needs a look; see the Health screen.'],
          ['Both steady (while holding the button)', 'Let go now to relearn the baseline.'],
          ['Both flashing together (while holding)', 'Let go now to open the Wi-Fi setup window.'],
          ['Green, double blink', 'The Wi-Fi setup window is open.']
        ]
      },
      {
        p: 'The browser tab icon blinks in the same colors. One extra pattern lives only there: a red double blink means the app cannot see the fan, or the fan wants your attention.'
      },
      { see: ['button', 'states', 'fix-fault'] }
    ]
  },
  {
    id: 'button',
    section: 'start',
    title: 'The button on the box',
    summary: 'Press for boost, hold 2 seconds to relearn, hold 8 seconds for Wi-Fi.',
    keywords: 'press hold long press short press physical button',
    blocks: [
      {
        rows: [
          ['Short press', 'Starts a boost (15 minutes by default). Press again to cancel it. If there is a fault, a short press clears it instead.'],
          ['Hold 2 seconds', 'Relearns the room’s normal levels. Both lights come on steady when you have held long enough. Let go then.'],
          ['Hold 8 seconds', 'Opens the Wi-Fi setup window for 3 minutes. Both lights flash together once you are past it.']
        ]
      },
      {
        note: 'The two holds are one progression. If you are trying to relearn, let go as soon as both lights are steady; if you keep holding until they flash, you open the Wi-Fi window instead. That does no harm, and it closes again by itself.'
      },
      { see: ['boost', 'relearn', 'wifi-change'] }
    ]
  },

  // --- Everyday use ---------------------------------------------------
  {
    id: 'boost',
    section: 'use',
    title: 'Boost',
    summary: 'Run the fan now, for a set time, whatever the sensors say.',
    keywords: 'manual run override on demand cancel timer',
    go: ['status-control', 'tune-timing'],
    blocks: [
      {
        p: '**Boost** on the Status screen (or a short press of the button) runs the fan for the boost length, 15 minutes by default. The time left counts down under the state.'
      },
      {
        p: '**Cancel boost** stops it early. When a boost ends, the fan goes back to automatic, so it keeps running if the room still needs it.'
      },
      { p: 'Change the boost length in Thresholds, under **Timing**.' },
      { see: ['modes', 'tune-timing'] }
    ]
  },
  {
    id: 'modes',
    section: 'use',
    title: 'Auto, Force on and Force off',
    summary: 'Overrides that hold until you change them back.',
    keywords: 'mode force on force off auto override always off always on disable',
    go: ['status-control', 'tune-mode'],
    blocks: [
      {
        rows: [
          ['Auto', 'Normal. The fan decides by itself.'],
          ['Force on', 'The fan runs continuously, for airing out the room.'],
          ['Force off', 'The fan stays off, for example during a long bath or while a sensor is misbehaving.']
        ]
      },
      {
        note: 'Force on and Force off stay set, even through a power cut, until you switch back to Auto. A fan that “never runs” is often a fan left on Force off.'
      },
      {
        p: 'A fault still wins over Force on: driving a fan that has stalled would only stall it again.'
      },
      { see: ['boost', 'fix-never-runs'] }
    ]
  },
  {
    id: 'relearn',
    section: 'use',
    title: 'Relearning the baseline',
    summary: 'When and how to reset what the fan thinks is normal.',
    keywords: 'baseline relearn reset normal calibrate recalibrate learn',
    go: ['status-control'],
    blocks: [
      {
        p: 'The baseline is the room’s normal humidity (and normal air, for the odor sensor). The fan learns it by itself and only updates it while the room is quiet, so a shower cannot teach it that steam is normal.'
      },
      { p: 'Relearn it by hand when:' },
      {
        list: [
          'you have moved the unit, or the room has changed (a new window, a dehumidifier);',
          'there was a power cut in the middle of a shower;',
          'the fan keeps running on air that is plainly fine.'
        ]
      },
      {
        steps: [
          'Make sure the room is in its normal state: no recent shower, door as usual.',
          'Tap **Relearn baseline** on the Status screen, or hold the button on the box for 2 seconds.',
          'The green light blinks for about a minute while it learns.'
        ]
      },
      {
        note: 'Relearning in a steamy room teaches it that steam is normal, and the fan will not run for the next shower. Wait for the room to dry first.'
      },
      { see: ['fix-runs-long', 'fix-odor'] }
    ]
  },
  {
    id: 'states',
    section: 'use',
    title: 'What the states mean',
    summary: 'LEARN, IDLE, RUN, BOOST and FAULT.',
    keywords: 'state learn idle run boost fault controller',
    go: ['status'],
    blocks: [
      {
        rows: [
          ['LEARN', 'Building the baseline. Takes about a minute after power-on or a relearn.'],
          ['IDLE', 'Watching, fan off.'],
          ['RUN', 'The fan is running because of humidity, light or odor. The line underneath says which.'],
          ['BOOST', 'You asked for a run. It ends after the boost length.'],
          ['FAULT', 'Something is wrong with the fan or a sensor. The fan is switched off to protect it.']
        ]
      },
      { see: ['lights', 'fix-fault'] }
    ]
  },
  {
    id: 'readings',
    section: 'use',
    title: 'What the readings mean',
    summary: 'Humidity, dew point, light, odor, NOx and fan speed.',
    keywords: 'readings humidity temperature dew point light lux voc nox odor rpm speed sensor',
    go: ['readings'],
    blocks: [
      {
        rows: [
          ['Humidity', 'Relative humidity in the room, and how far it is above the baseline. The “above baseline” figure is what starts the fan.'],
          ['Temperature', 'The room temperature, from the same sensor.'],
          ['Dew point', 'The temperature at which moisture would condense on a surface. Cold walls below it are where mildew starts.'],
          ['Light', 'A raw brightness reading. The fan looks for the jump a light switch makes, not for a level, so daylight does not fool it.'],
          ['Odor', 'How far the air has moved from its clean level, in sensor ticks. Quiet air wanders under about 40; a real event reads around 100.'],
          ['NOx', 'A second gas reading. A gas flame makes NOx and a bathroom does not, so a big NOx rise means the smell is cooking somewhere else.'],
          ['Fan speed', 'Revolutions per minute while the fan runs. This is how the controller knows the fan is really turning.']
        ]
      },
      {
        p: 'The odor sensor needs up to two hours after power-on before its readings count. Until then it shows as settling, and light and humidity still run the fan as usual.'
      },
      { see: ['tune-odor', 'charts'] }
    ]
  },
  {
    id: 'charts',
    section: 'use',
    title: 'Charts and history',
    summary: 'A week of readings, stored on the fan itself.',
    keywords: 'charts history graph week past trend',
    go: ['charts'],
    blocks: [
      {
        p: 'The fan keeps about a week of readings in its own memory, one row every 5 minutes, so the charts work without Home Assistant and survive the app being closed. While the app is open it also records the live readings, which fills in the detail.'
      },
      {
        p: 'Tap a chart to open it large. Gaps are times the fan was off or unreachable.'
      }
    ]
  },

  // --- Thresholds -----------------------------------------------------
  {
    id: 'tune-humidity',
    section: 'tune',
    title: 'Humidity thresholds',
    summary: 'When a shower starts the fan, and when it is dry enough to stop.',
    keywords: 'humidity start stop rise fall threshold shower steam rh',
    go: ['tune-humidity'],
    blocks: [
      {
        rows: [
          ['Start at', 'How far above the baseline the humidity must climb to start the fan. 8 % by default.'],
          ['Stop below', 'How far it must fall back before the fan may stop. 3 % by default.']
        ]
      },
      {
        p: 'The gap between the two keeps the fan from flicking on and off around one number. Keep **Stop below** well under **Start at**.'
      },
      {
        list: [
          'Fan starts too late in a shower: lower **Start at** a step or two.',
          'Fan starts on a humid day with nobody in the room: raise **Start at**.',
          'Fan stops while the mirror is still fogged: lower **Stop below**.',
          'Fan runs far longer than the room needs: raise **Stop below** a little.'
        ]
      },
      { see: ['tune-timing', 'fix-runs-long'] }
    ]
  },
  {
    id: 'tune-light',
    section: 'tune',
    title: 'Light thresholds',
    summary: 'How the fan spots the light switch, and how long light alone may run it.',
    keywords: 'light switch lamp bulb step rise threshold daylight',
    go: ['tune-light'],
    blocks: [
      {
        p: 'The fan does not look at how bright the room is. It looks for the **jump** a light switch makes, measured against the room’s learned ambient light. That way sunlight through a window does not count as somebody in the room.'
      },
      {
        rows: [
          ['Switch step', 'The jump in one reading that counts as the switch being flipped.'],
          ['Rise to start', 'How far above ambient the light must land for the fan to start.'],
          ['Rise to stop', 'Once the light is back within this of ambient, it counts as off.'],
          ['Light-only run', 'How long the light alone keeps the fan on (10 minutes by default). After that, the humidity has to justify running.']
        ]
      },
      {
        p: 'After the light goes out, the fan keeps going for at least **After lights out** (under Timing) and until the humidity is back down.'
      },
      {
        note: 'If the fan does not react to the light at all, the switch step is probably larger than your bulb makes. Lower **Switch step** and **Rise to start** together.'
      },
      { see: ['tune-timing'] }
    ]
  },
  {
    id: 'tune-odor',
    section: 'tune',
    title: 'Odor thresholds',
    summary: 'Letting the air-quality sensor run the fan.',
    keywords: 'odor smell voc nox veto threshold air quality sensor gas',
    go: ['tune-odor', 'readings'],
    blocks: [
      {
        p: 'Odor is measured in sensor ticks away from the room’s clean-air level. What matters is the size of the change, not an absolute smell scale. Quiet air wanders under about 40 ticks; a real event in a bathroom reads around 100.'
      },
      {
        rows: [
          ['Start at', 'Ticks of change that start the fan. **0 means odor never starts the fan**, which is the default, so you can watch the sensor before trusting it.'],
          ['Stop below', 'The air counts as clear again once the change is back under this.'],
          ['Ignore if burning', 'If the NOx reading rises by this much, the smell is taken to be cooking or a gas flame elsewhere, and the fan sits it out.']
        ]
      },
      {
        steps: [
          'Leave **Start at** on 0 for a few days and watch Odor on the Readings screen.',
          'Note what quiet air reads, and what a real event reads.',
          'Set **Start at** between the two (this project’s own bathroom uses 70), and **Stop below** a little above what quiet air reads.'
        ]
      },
      {
        p: 'Right after a shower, odor is ignored for a while: drying a wet room moves the gas reading by itself, and that is not a second reason to run.'
      },
      { see: ['readings', 'fix-odor'] }
    ]
  },
  {
    id: 'tune-timing',
    section: 'tune',
    title: 'Timing',
    summary: 'Minimum and maximum run, after lights out, and boost length.',
    keywords: 'timing minimum maximum max run min run boost length after lights out timer cap',
    go: ['tune-timing'],
    blocks: [
      {
        rows: [
          ['Minimum run', 'Once started, the fan runs at least this long, so it never flicks on and off. 5 minutes by default.'],
          ['Maximum run', 'A hard stop, whatever the sensors say. 45 minutes by default.'],
          ['After lights out', 'Keep running at least this long after the light goes off. 2 minutes by default.'],
          ['Boost length', 'How long a boost lasts. 15 minutes by default.']
        ]
      },
      {
        p: 'When a run hits **Maximum run**, the fan stops and stays stopped until the room actually dries, the light comes back on, or you boost or relearn. Without that pause it would simply start another full run straight away.'
      },
      {
        note: 'If long showers regularly hit the maximum and the room is still damp, raise Maximum run rather than loosening the humidity thresholds.'
      },
      { see: ['tune-humidity', 'fix-runs-long'] }
    ]
  },
  {
    id: 'tune-reset',
    section: 'tune',
    title: 'Changing and resetting values',
    summary: 'How changes are checked, and how to undo them.',
    keywords: 'reset defaults change value rejected range save',
    go: ['tune-mode'],
    blocks: [
      {
        p: 'Use the − and + buttons or type a value. The fan checks every value against its own safe range and refuses anything outside it. A change only counts once it shows up in the next reading, so if a number snaps back, the fan rejected it.'
      },
      {
        p: '**Reset to defaults**, at the bottom of Thresholds, puts every value back to the firmware’s own. Tap it twice to confirm. Your connection, units and color scheme are not touched.'
      },
      { p: 'Settings are stored on the fan, so they survive power cuts and apply to every phone that opens the app.' }
    ]
  },

  // --- Setup and connections ------------------------------------------
  {
    id: 'setup-first',
    section: 'setup',
    title: 'Setting up a new fan',
    summary: 'Joining it to your Wi-Fi with a phone, from the box’s own setup network.',
    keywords: 'first setup install new provision wifi network label smartfan-xxxx captive portal room country',
    go: ['settings-connection'],
    blocks: [
      {
        steps: [
          'Power the fan up. A new fan with no Wi-Fi saved starts its own setup network.',
          'On your phone, join the network named **Smartfan-XXXX**. The name and its password are on the label on the box.',
          'A setup page opens by itself. If it does not, open any web page and it will appear.',
          'Pick your home Wi-Fi, enter its password, and type the **room** (for example “Bathroom” or “Master bath”). Check the country.',
          'The fan joins your Wi-Fi, and the setup network disappears. That is expected: it means the join worked.',
          'Rejoin your home Wi-Fi on the phone and open the address the page showed you, such as **http://bathroom-fan.local/**.'
        ]
      },
      {
        note: 'Nothing is saved until the fan has joined successfully, so a wrong password only costs you a retry.'
      },
      {
        p: 'The room name decides the fan’s address: “Master Bath” becomes **master-bath-fan.local**. Give each fan in the house its own room name.'
      },
      { see: ['connect', 'wifi-change'] }
    ]
  },
  {
    id: 'connect',
    section: 'setup',
    title: 'Connecting the app to the fan',
    summary: 'The fan itself, or through an MQTT broker.',
    keywords: 'connect connection address fan.local settings broker device mode',
    go: ['settings-connection'],
    blocks: [
      { p: 'Open **Settings** at the top. There are two ways to reach the fan:' },
      {
        rows: [
          ['The fan itself', 'The app talks straight to the fan at its address (for example fan.local). Nothing else needs to be running. Recommended for most homes.'],
          ['An MQTT broker', 'The app listens through your broker, usually Home Assistant’s Mosquitto add-on. Use this if you want to reach the fan the same way Home Assistant does.']
        ]
      },
      {
        p: 'If you opened the app from the fan’s own address, it is already set to the fan itself and needs nothing more.'
      },
      {
        note: 'Phones and computers find **.local** addresses on the same Wi-Fi only. Away from home, or on a guest network, the address will not resolve.'
      },
      { see: ['ha-choose', 'fix-cant-connect'] }
    ]
  },
  {
    id: 'wifi-change',
    section: 'setup',
    title: 'Changing the fan’s Wi-Fi',
    summary: 'Moving the fan to a new network or password from the app.',
    keywords: 'change wifi new router password network ssid move',
    go: ['settings-network'],
    blocks: [
      {
        steps: [
          'Connect the app to **the fan itself** (Settings, top). Changing Wi-Fi through a broker is not possible.',
          'At the fan, hold the button for 8 seconds, until both lights flash together, then let go. This unlocks Wi-Fi changes for 3 minutes.',
          'In Settings, under **Network**, tap **Scan**, pick the network, enter its password, and tap **Join this network**.',
          'The fan tries the new network before saving anything. If it works, the fan leaves your current network: join the new one on your phone and open the fan’s address again.'
        ]
      },
      {
        note: 'If the fan cannot join the new network, it goes back to the old one and nothing is changed.'
      },
      {
        p: 'The button hold is deliberate: it means only someone standing at the fan can move it to another network.'
      },
      { see: ['button', 'setup-first'] }
    ]
  },
  {
    id: 'ha-choose',
    section: 'setup',
    title: 'Home Assistant: choosing how to connect',
    summary: 'Through your MQTT broker, or with the Smartfan integration. Pick one.',
    keywords: 'home assistant ha integration mqtt which choose compare broker',
    blocks: [
      {
        p: 'Home Assistant is optional: the fan and this app work fully without it. If you use it, there are two ways to bring the fan in. Both give you its readings, controls and thresholds as entities.'
      },
      {
        rows: [
          ['Through MQTT', 'Nothing to install in Home Assistant. Needs an MQTT broker, usually the Mosquitto add-on. The fan announces itself and appears by itself.'],
          ['Smartfan integration', 'No broker needed. Install it once (through HACS or by hand), then add the fan by its address. Home Assistant asks the fan directly every 10 seconds, and it also shows the controller’s own reason for what it is doing.']
        ]
      },
      {
        list: [
          'Already run Mosquitto? MQTT is the simplest.',
          'No broker, and you would rather not add one? Use the integration.'
        ]
      },
      {
        note: 'Pick one. With both, Home Assistant shows two copies of every entity for the same fan. If you switch to the integration, delete the MQTT device in Home Assistant.'
      },
      { see: ['ha', 'ha-integration'] }
    ]
  },
  {
    id: 'ha-integration',
    section: 'setup',
    title: 'Installing the Smartfan integration',
    summary: 'Download it into Home Assistant, then add the fan by its address.',
    keywords: 'home assistant ha integration hacs install download custom component add device setup config flow',
    blocks: [
      { p: '**Install it with HACS** (the Home Assistant Community Store), which also tells you when there is an update:' },
      {
        steps: [
          'If you do not have HACS yet, install it first (link below).',
          'Tap **Add to HACS** below. It opens your own Home Assistant with the Smartfan repository filled in. The first time, it asks for your Home Assistant’s address.',
          'Tap **Download**, then restart Home Assistant when it asks.'
        ]
      },
      {
        links: [
          { href: `https://my.home-assistant.io/redirect/hacs_repository/?owner=${REPO_OWNER}&repository=${REPO_NAME}&category=integration`, label: 'Add to HACS', note: 'Opens HACS in your Home Assistant' },
          { href: 'https://hacs.xyz/docs/use/', label: 'Get HACS', note: 'How to install HACS itself' }
        ]
      },
      {
        p: 'If the button does not work for you, add it in HACS yourself: **HACS › ⋮ › Custom repositories**, paste the repository address below, and choose type **Integration**.'
      },
      {
        p: 'Not using HACS at all? Download the project, copy its **custom_components/smartfan** folder into the **custom_components** folder of your Home Assistant configuration (create it if it is missing), and restart Home Assistant. Updates are then up to you.'
      },
      {
        links: [
          { href: `${REPO_URL}/archive/refs/heads/main.zip`, label: 'Download the project (ZIP)', note: 'For installing by hand' },
          { href: REPO_URL, label: 'Repository', note: REPO_URL.replace('https://', '') }
        ]
      },
      { p: '**Then add the fan:**' },
      {
        steps: [
          'Tap **Add the fan to Home Assistant** below, or go to **Settings › Devices & services › Add integration** and search for **Smartfan**.',
          'Enter the fan’s address without http://, for example **fan.local**, **bathroom-fan.local**, or its IP address.',
          'Home Assistant checks it can reach the fan before adding anything. The device then appears with all its entities.'
        ]
      },
      {
        links: [
          { href: 'https://my.home-assistant.io/redirect/config_flow_start/?domain=smartfan', label: 'Add the fan to Home Assistant', note: 'Opens the setup in your Home Assistant' }
        ]
      },
      {
        note: 'Use the fan’s **.local** name or its IP address. The fan refuses any other name, such as one your router invents (fan.lan), as a guard against malicious web pages.'
      },
      {
        rows: [
          ['“Nothing answered at that address”', 'The fan is off, on another network, or the name does not resolve from Home Assistant. Try the IP address.'],
          ['“Firmware newer than this integration understands”', 'Update the integration in HACS.'],
          ['Two sets of entities', 'The MQTT device is still there. Delete one of the two.']
        ]
      },
      {
        p: 'To remove it: **Settings › Devices & services › Smartfan › ⋮ › Delete**. The fan itself is untouched and carries on as before.'
      },
      { see: ['ha-choose', 'connect'] }
    ]
  },
  {
    id: 'ha',
    section: 'setup',
    title: 'Home Assistant through MQTT',
    summary: 'Getting the fan into Home Assistant through a broker.',
    keywords: 'home assistant ha mqtt mosquitto broker add-on entities 1883 1884 websocket login account',
    go: ['settings-broker', 'settings-connection'],
    blocks: [
      {
        p: 'A broker is optional. With one, the fan appears in Home Assistant by itself, with its readings, controls and thresholds as entities. Without one, the fan and this app still work fully. (No broker? The Smartfan integration is the other way in.)'
      },
      {
        steps: [
          'In Home Assistant, install the **Mosquitto broker** add-on if you do not have it.',
          'Give the fan its own login: in the add-on’s **Configuration**, under **Logins**, add a username and password for it, save, and restart the add-on.',
          'At the fan, hold the button for 8 seconds to unlock settings.',
          'In this app’s Settings (connected to **the fan itself**), under **Broker**, enter the broker address, port **1883**, and the fan’s username and password.',
          'The fan tests the login before saving. Within a minute the device appears in Home Assistant under the MQTT integration.'
        ]
      },
      {
        rows: [
          ['Port 1883', 'What the fan uses (plain MQTT). Goes in the fan’s Broker settings.'],
          ['Port 1884', 'What this app uses in broker mode (MQTT over WebSocket). Goes in the Connection settings.']
        ]
      },
      {
        note: 'Use a login from the add-on’s own list, not a Home Assistant user. The fan sends its password over your network unencrypted, and a Home Assistant user’s password also opens Home Assistant itself.'
      },
      { see: ['ha-choose', 'ha-integration', 'connect'] }
    ]
  },
  {
    id: 'updates',
    section: 'setup',
    title: 'Updates',
    summary: 'Keeping the app and the fan’s firmware current.',
    keywords: 'update upgrade firmware new version reload stale ota',
    go: ['health-device'],
    blocks: [
      {
        p: 'The fan serves this app from its own memory. When a newer app is on the fan than the one your browser loaded, a bar at the top says so. Tap **Reload** to get it.'
      },
      {
        p: 'Firmware updates are installed over Wi-Fi by whoever built the fan. The fan only accepts updates signed with its own key, so nobody else on your network can install software on it. If an update fails, the fan goes back to the version it was running.'
      },
      {
        p: 'The Health screen shows the firmware and app versions, which is useful if you are asked “what version is it on?”.'
      }
    ]
  },

  // --- Troubleshooting ------------------------------------------------
  {
    id: 'fix-cant-connect',
    section: 'fix',
    title: 'The app cannot reach the fan',
    summary: 'Stuck on Connecting, or an error at the top.',
    keywords: 'cannot connect connecting error waiting first reading not loading unreachable fan.local',
    go: ['settings-connection'],
    blocks: [
      { p: 'Work down this list:' },
      {
        steps: [
          'Check the lights on the box. Any steady or blinking light means it has power and is running, with or without the app.',
          'Make sure your phone is on the same Wi-Fi as the fan, not a guest network or mobile data.',
          'Open Settings and check the fan address. Try the fan’s address in a new browser tab, such as http://fan.local/.',
          'If .local addresses do not work on this device, use the fan’s IP address instead. Your router’s list of connected devices shows it.',
          'In broker mode: check the broker address, port 1884, and the username and password. An error mentioning login means the broker refused them.',
          'Still nothing: power the fan off and on. It reconnects by itself, and if the network stays unreachable for 15 minutes it restarts itself.'
        ]
      },
      { see: ['connect', 'fix-offline', 'fix-wifi'] }
    ]
  },
  {
    id: 'fix-offline',
    section: 'fix',
    title: '“Fan offline” or “No update”',
    summary: 'The app connected, but readings have stopped.',
    keywords: 'offline no update stale stopped reporting not responding',
    go: ['health-link'],
    blocks: [
      {
        p: 'This is a reporting problem, not a fan problem. The fan keeps running on its own logic whether or not anything can hear it.'
      },
      {
        list: [
          '**No update** means nothing has arrived for over 90 seconds. Often a brief Wi-Fi drop that clears by itself.',
          '**Fan offline** means the fan stopped answering (or, in broker mode, the broker reports it gone).'
        ]
      },
      {
        p: 'If it lasts more than a few minutes, look at the box: lights on means it is running fine and only the network is the problem. Check Wi-Fi signal on the Health screen once it is back.'
      },
      { see: ['fix-wifi', 'fix-cant-connect'] }
    ]
  },
  {
    id: 'fix-fault',
    section: 'fix',
    title: 'The fan shows FAULT',
    summary: 'What each fault means and what to do.',
    keywords: 'fault error alternating lights clear fault health',
    go: ['health-device', 'status-control'],
    blocks: [
      {
        p: 'A fault switches the fan off and makes the lights alternate. The Health screen, under **Fault**, gives the reason. Once the cause is fixed, clear it with **Clear fault** on the Status screen or a short press of the button.'
      },
      {
        see: ['fix-fault-stall', 'fix-fault-stuck', 'fix-fault-sensor', 'fix-fault-selftest']
      }
    ]
  },
  {
    id: 'fix-fault-stall',
    section: 'fix',
    title: 'Fault: fan stalled',
    summary: 'The fan was switched on but did not turn.',
    keywords: 'stalled stall 0 rpm zero rpm not spinning not turning latched seized',
    go: ['readings', 'status-control'],
    blocks: [
      {
        p: 'The controller switched the fan on and measured no speed (or too little). It turns the fan off, waits 5 minutes and tries again. After 3 stalls in a row it stops trying and shows **LATCHED**.'
      },
      {
        steps: [
          'Listen at the fan when it should be running. Silent, or humming without turning?',
          'Check that nothing is blocking the blades.',
          'Check the fan’s wires at the controller: a loose connection is the most common cause, especially one that comes and goes.',
          'Clear the fault (short press, or Clear fault) and watch the fan speed on the Readings screen.'
        ]
      },
      {
        note: 'A stall that comes and goes, with speed sometimes reading 0, points at a loose wire or connector rather than a dead fan.'
      },
      { see: ['fix-fault'] }
    ]
  },
  {
    id: 'fix-fault-stuck',
    section: 'fix',
    title: 'Fault: fan still running after it was switched off',
    summary: 'The controller cannot turn the fan off.',
    keywords: 'stuck still running wont stop cannot turn off wiring ground',
    go: ['health-device'],
    blocks: [
      {
        p: 'The fan kept turning more than 12 seconds after it was told to stop. That means the switch in the controller is not in control of the fan, which is almost always a wiring mistake: the fan’s ground (black) wire is connected somewhere that bypasses the switch.'
      },
      {
        p: 'This cannot be fixed from the app. Switch the power off and have whoever installed the fan check the black wire goes to the controller’s fan terminal, not straight to ground.'
      },
      { see: ['fix-fault'] }
    ]
  },
  {
    id: 'fix-fault-sensor',
    section: 'fix',
    title: 'Fault: humidity sensor not responding',
    summary: 'The fan cannot read the humidity, so it stops guessing.',
    keywords: 'sensor dht22 humidity sensor not responding no reading failed',
    go: ['status-control', 'health-device'],
    blocks: [
      {
        p: 'After several failed readings in a row the controller stops trusting the humidity and raises a fault, rather than run the fan on guesswork.'
      },
      {
        steps: [
          'Clear the fault and see whether it comes back.',
          'If it does, power the fan off and check the sensor’s wires and plug.',
          'Condensation on the sensor board can also cause it: if the fault came during a shower, let the room dry and try again.'
        ]
      },
      { see: ['fix-fault'] }
    ]
  },
  {
    id: 'fix-fault-selftest',
    section: 'fix',
    title: 'Fault: fan did not turn at power-on',
    summary: 'The start-up fan test failed.',
    keywords: 'self test selftest power on startup test failed fan test',
    go: ['health-device', 'status-control'],
    blocks: [
      {
        p: 'Every time the fan gets power, it runs the fan for a few seconds to check it turns. If no speed comes back, it raises this fault, so a dead fan is found while somebody is nearby, not days later at the next shower.'
      },
      {
        p: 'Check the same things as for a stall: blades free, fan wires firmly connected, and the fan’s own power. The Health screen shows the result as **Fan test**.'
      },
      {
        p: '**Never run** on that line means the fan was on Force off at power-on, so the test waited. It runs once you switch back to Auto.'
      },
      { see: ['fix-fault-stall', 'modes'] }
    ]
  },
  {
    id: 'fix-runs-long',
    section: 'fix',
    title: 'The fan runs too long, or will not stop',
    summary: 'Usually a baseline learned in the wrong conditions, or a mode left on.',
    keywords: 'runs too long wont stop always on never stops keeps running',
    go: ['status', 'tune-mode', 'tune-humidity'],
    blocks: [
      {
        steps: [
          'Check the mode is **Auto**, not Force on.',
          'Look at the line under the state on the Status screen. It names the reason the fan is running.',
          'If it says humidity but the room is dry, the baseline is off. Relearn it in a dry room.',
          'If it says odor, see “The fan runs on odor when nothing smells”.',
          'If runs always last exactly the maximum, the humidity is not falling to **Stop below**. Raise Stop below slightly, or check the door and the duct.'
        ]
      },
      { see: ['relearn', 'tune-humidity', 'fix-odor'] }
    ]
  },
  {
    id: 'fix-never-runs',
    section: 'fix',
    title: 'The fan does not run when it should',
    summary: 'A shower, and nothing happens.',
    keywords: 'never runs doesnt start does not run not starting no fan shower',
    go: ['status-control', 'tune-humidity'],
    blocks: [
      {
        steps: [
          'Check the mode is **Auto**, not Force off.',
          'Check the state is not FAULT (lights alternating).',
          'Tap **Boost**. If the fan does not turn at all, the problem is the fan or its wiring, not the settings.',
          'If boost works, watch the gauge during a shower. If it never reaches the red band, lower **Start at** under Humidity.',
          'If the fan was relearned during a shower, the baseline is too high. Relearn it in a dry room.'
        ]
      },
      { see: ['modes', 'tune-humidity', 'relearn'] }
    ]
  },
  {
    id: 'fix-odor',
    section: 'fix',
    title: 'The fan runs on odor when nothing smells',
    summary: 'Tuning or relearning the air-quality sensor.',
    keywords: 'odor false trigger smell voc runs for no reason cooking',
    go: ['tune-odor', 'readings'],
    blocks: [
      {
        list: [
          'Cleaning products, perfume and air freshener all register as odor. That is the sensor working.',
          'Cooking elsewhere in the house can drift in. Lowering **Ignore if burning** makes the fan sit out more of it.',
          'If quiet air reads well above the start line, the clean-air level is off. Relearn the baseline with the room aired out.',
          'To stop odor running the fan entirely, set its **Start at** to 0.'
        ]
      },
      { see: ['tune-odor', 'relearn'] }
    ]
  },
  {
    id: 'fix-wifi',
    section: 'fix',
    title: 'Weak Wi-Fi or frequent reconnects',
    summary: 'Reading the Link panel on the Health screen.',
    keywords: 'wifi weak signal rssi dbm reconnects outage dropouts router',
    go: ['health-link'],
    blocks: [
      {
        rows: [
          ['Strong or Good', 'Fine.'],
          ['Weak', 'Works, but expect the odd dropout.'],
          ['Very weak', 'Expect regular dropouts. Move the router or add an access point nearer the bathroom.']
        ]
      },
      {
        p: '**Reconnects** counts how often the fan has lost and regained the network since it started; **Last outage** says how long the most recent one lasted. A few a day is normal. Dozens point to a signal problem.'
      },
      {
        p: 'None of this affects the fan’s own behavior, only how well you can see it.'
      }
    ]
  },
  {
    id: 'fix-more',
    section: 'fix',
    title: 'Getting more help',
    summary: 'What to note down before asking whoever built or installed it.',
    keywords: 'support contact more help diagnostics report version',
    go: ['health-device', 'status'],
    blocks: [
      { p: 'Before asking for help, note:' },
      {
        list: [
          'what the lights on the box are doing;',
          'the state and reason on the Status screen;',
          'from the Health screen: the fault text, Fan test, Last restart, Firmware and Wi-Fi signal;',
          'roughly when it started, and what was happening (a shower, a power cut, a new router).'
        ]
      },
      {
        p: 'For builders: the project’s troubleshooting page decodes the fan’s USB log line by line and walks the wiring fault tree.'
      }
    ]
  }
];

/** Topic lookup by id. */
export const BY_ID = Object.fromEntries(TOPICS.map((t) => [t.id, t]));

/**
 * The help topic that explains a fault, from its reason text.
 *
 * The firmware publishes a sentence, not a code (check_fan_health), so this
 * matches the start of each one. An unknown reason lands on the general
 * fault page rather than nowhere.
 */
export function faultTopic(reason) {
  const r = String(reason ?? '').toLowerCase();
  if (r.startsWith('fan stalled')) return 'fix-fault-stall';
  if (r.startsWith('fan still running')) return 'fix-fault-stuck';
  if (r.startsWith('fan did not turn')) return 'fix-fault-selftest';
  if (r.includes('not responding')) return 'fix-fault-sensor';
  return 'fix-fault';
}

/** Help topic for each Thresholds group, keyed by the group's title. */
export const TUNE_TOPIC = {
  Humidity: 'tune-humidity',
  Light: 'tune-light',
  Odor: 'tune-odor',
  Timing: 'tune-timing'
};

/** Every word of a topic, lowercased, for search. */
function haystack(t) {
  const parts = [t.title, t.summary, t.keywords];
  for (const b of t.blocks) {
    if (b.p) parts.push(b.p);
    if (b.note) parts.push(b.note);
    for (const s of b.steps ?? []) parts.push(s);
    for (const s of b.list ?? []) parts.push(s);
    for (const [a, c] of b.rows ?? []) parts.push(a, c);
    for (const l of b.links ?? []) parts.push(l.label, l.note ?? '');
  }
  return parts.join(' ').replace(/\*\*/g, '').toLowerCase();
}

const HAY = Object.fromEntries(TOPICS.map((t) => [t.id, haystack(t)]));

/**
 * Topics matching a search, best first.
 *
 * Every word typed must appear somewhere in the topic. Ranking: a word in
 * the title beats one in the keywords or summary, which beats one in the
 * body. Words match from their start, so "stal" finds "stalled" while
 * typing but "stalled" does not find "installed".
 */
export function search(query) {
  const words = String(query ?? '')
    .toLowerCase()
    .split(/\s+/)
    .map((w) => w.replace(/[^\p{L}\p{N}.-]/gu, ''))
    .filter(Boolean);
  if (!words.length) return [];
  const pats = words.map(
    (w) => new RegExp('(?:^|[^\\p{L}\\p{N}])' + w.replace(/[.-]/g, '\\$&'), 'u')
  );
  const hits = [];
  for (const t of TOPICS) {
    if (!pats.every((p) => p.test(HAY[t.id]))) continue;
    const title = t.title.toLowerCase();
    const near = `${t.summary} ${t.keywords}`.toLowerCase();
    let score = 0;
    for (const p of pats) score += p.test(title) ? 3 : p.test(near) ? 2 : 1;
    hits.push({ t, score });
  }
  hits.sort((a, b) => b.score - a.score);
  return hits.map((h) => h.t);
}

/**
 * Split a sentence on **strong** markers into [{ text, strong }] runs, so the
 * sheet can render emphasis with real elements instead of {@html}.
 */
export function runs(text) {
  return String(text)
    .split('**')
    .map((s, i) => ({ text: s, strong: i % 2 === 1 }))
    .filter((r) => r.text);
}
