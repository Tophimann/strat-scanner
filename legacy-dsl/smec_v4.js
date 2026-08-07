//@version=1
// SMEC° v8.6

let lastIds = [];
let lastKey1 = null;
let lastKey2 = null;
let lastKey3 = null;
let lastLength = 0;

function tfToMs(tf) {
  if (tf === '1m')  return 60000;
  if (tf === '5m')  return 300000;
  if (tf === '15m') return 900000;
  if (tf === '30m') return 1800000;
  if (tf === '1h')  return 3600000;
  if (tf === '2h')  return 7200000;
  if (tf === '4h')  return 14400000;
  if (tf === '1d')  return 86400000;
  if (tf === '1w')  return 604800000;
  return 3600000;
}

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });

  // TF 1
  input.str('HTF 1', '1h', 'tf1',
    ['1m','5m','15m','30m','1h','2h','4h','1d','1w'],
    'Timeframe 1', 'TF 1');
  input.color('Bull-Farbe 1', color.rgba(0,   160, 120, 1), 'colorBull1', 'TF 1');
  input.color('Bear-Farbe 1', color.rgba(180, 40,  40,  1), 'colorBear1', 'TF 1');

  // TF 2
  input.bool('TF 2 aktiv', false, 'enabled2', 'TF 2');
  input.str('HTF 2', '4h', 'tf2',
    ['1m','5m','15m','30m','1h','2h','4h','1d','1w'],
    'Timeframe 2', 'TF 2');
  input.color('Bull-Farbe 2', color.rgba(0,   100, 220, 1), 'colorBull2', 'TF 2');
  input.color('Bear-Farbe 2', color.rgba(220, 120, 0,   1), 'colorBear2', 'TF 2');

  // TF 3
  input.bool('TF 3 aktiv', false, 'enabled3', 'TF 3');
  input.str('HTF 3', '1d', 'tf3',
    ['1m','5m','15m','30m','1h','2h','4h','1d','1w'],
    'Timeframe 3', 'TF 3');
  input.color('Bull-Farbe 3', color.rgba(180, 160, 0,   1), 'colorBull3', 'TF 3');
  input.color('Bear-Farbe 3', color.rgba(120, 0,   180, 1), 'colorBear3', 'TF 3');

  // Gemeinsam
  input.color('Linien-Farbe', color.rgba(255, 255, 255, 1), 'colorLine', 'Farben');
  input.str('Linienart', 'solid', 'lineStyle',
    ['solid', 'dotted', 'dashed'], 'Stil der Quartillinien', 'Farben');
};

onTick = (length, _moment, _, ta, inputs) => {
try {
  if (!length || length < 3) return;
  var t0 = time(0);
  if (!t0 || t0 <= 0) return;

  var tfMs1 = tfToMs(inputs.tf1);
  var tfMs2 = inputs.enabled2 ? tfToMs(inputs.tf2) : 0;
  var tfMs3 = inputs.enabled3 ? tfToMs(inputs.tf3) : 0;

  var key1 = Math.floor(t0 / tfMs1) * tfMs1;
  var key2 = tfMs2 > 0 ? Math.floor(t0 / tfMs2) * tfMs2 : -1;
  var key3 = tfMs3 > 0 ? Math.floor(t0 / tfMs3) * tfMs3 : -1;

  if (key1 === lastKey1 && key2 === lastKey2 && key3 === lastKey3 && length === lastLength) return;
  lastKey1 = key1; lastKey2 = key2; lastKey3 = key3; lastLength = length;

  for (var k = 0; k < lastIds.length; k++) {
    try { deleteDrawingById(lastIds[k]); } catch(e) {}
  }
  lastIds = [];

  var ls = inputs.lineStyle === 'dotted' ? 1 : inputs.lineStyle === 'dashed' ? 2 : 0;

  // Alle 3 TFs verarbeiten
  var configs = [
    { tfMs: tfMs1, curKey: key1, bull: inputs.colorBull1, bear: inputs.colorBear1 },
    { tfMs: tfMs2, curKey: key2, bull: inputs.colorBull2, bear: inputs.colorBear2 },
    { tfMs: tfMs3, curKey: key3, bull: inputs.colorBull3, bear: inputs.colorBear3 },
  ];

  for (var ci = 0; ci < configs.length; ci++) {
    var cfg = configs[ci];
    if (cfg.tfMs <= 0) continue;

    // LTF-Bars in HTF-Buckets aggregieren
    var htfBars = {};
    for (var i = 1; i < length; i++) {
      var t = time(i);
      if (!t || t <= 0) continue;
      var htfKey = Math.floor(t / cfg.tfMs) * cfg.tfMs;
      var h = high(i);
      var l = low(i);
      if (!h || h <= 0) continue;
      if (!htfBars[htfKey]) {
        htfBars[htfKey] = { o: openC(i), h: h, l: l, c: closeC(i) };
      } else {
        htfBars[htfKey].o = openC(i);
        if (h > htfBars[htfKey].h) htfBars[htfKey].h = h;
        if (l < htfBars[htfKey].l) htfBars[htfKey].l = l;
      }
    }

    // Keys absteigend sortieren
    var htfKeys = [];
    for (var key in htfBars) htfKeys.push(Number(key));
    htfKeys.sort(function(a, b) { return b - a; });

    var si = (htfKeys.length > 0 && htfKeys[0] === cfg.curKey) ? 1 : 0;
    if (si + 1 >= htfKeys.length) continue;

    // Neueste Engulfing-Bar suchen
    for (var j = si; j < htfKeys.length - 1; j++) {
      var engK = htfKeys[j];
      var refK = htfKeys[j + 1];
      var curr = htfBars[engK];
      var prev = htfBars[refK];

      var dir = null;
      if (curr.l < prev.l && curr.c > prev.o) dir = 'bull';
      else if (curr.h > prev.h && curr.c < prev.o) dir = 'bear';
      if (!dir) continue;

      var et       = engK + cfg.tfMs;
      var boxColor = dir === 'bull' ? cfg.bull : cfg.bear;

      try {
        var id = rectangle(engK, curr.h, et, curr.l, {
          backgroundColor: boxColor,
          fillBackground:  true,
          color:           boxColor,
          linewidth:       1,
        });
        if (id !== null && id !== undefined) lastIds.push(id);
      } catch(e) {}

      var range   = curr.h - curr.l;
      var qLevels = [curr.h - range * 0.25, curr.h - range * 0.50, curr.h - range * 0.75];
      for (var q = 0; q < qLevels.length; q++) {
        var price = qLevels[q];
        try {
          var lid = trendLine({ time: engK, price: price }, { time: et, price: price }, {
            linecolor: inputs.colorLine,
            linewidth:  1,
            linestyle:  ls,
          });
          if (lid !== null && lid !== undefined) lastIds.push(lid);
        } catch(e) {}
      }

      break;
    }
  }

} catch(err) {}
};
