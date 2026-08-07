//@version=1
// SMEC Debug v2 – HorzlineLineToolOverrides fix

let pT  = null;
let ids = [];

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });
};

onTick = (length, _moment, _, ta, inputs) => {
  try {
    var t = time(0);
    if (t === pT) return;
    pT = t;

    ids.forEach(function(id) { try { deleteDrawingById(id); } catch(e) {} });
    ids = [];

    var h  = high(0);
    var lo = low(0);
    var cl = closeC(0);

    var offMs = 3600000;
    var offS  = 3600;

    // Test A: rectangle (ms) mit style
    try {
      var a = rectangle(t - offMs, h, t, lo, {
        backgroundColor: color.rgba(255, 0, 0, 0.4),
      });
      if (a != null) ids.push(a);
    } catch(e) {}

    // Test B: rectangle (s) mit style – andere Preisebene
    try {
      var b = rectangle(t - offS, h * 1.002, t, lo * 0.998, {
        backgroundColor: color.rgba(0, 0, 255, 0.4),
      });
      if (b != null) ids.push(b);
    } catch(e) {}

    // Test C: rectangle OHNE style (ms)
    try {
      var c = rectangle(t - offMs * 2, h * 1.004, t, lo * 0.996);
      if (c != null) ids.push(c);
    } catch(e) {}

    // Test D: horizontalLine mit linecolor-Objekt (ms)
    try {
      var d = horizontalLine(t - offMs, cl, { linecolor: color.rgba(255, 165, 0, 1) });
      if (d != null) ids.push(d);
    } catch(e) {}

    // Test E: horizontalLine mit linecolor-Objekt (s)
    try {
      var e = horizontalLine(t - offS, cl * 1.003, { linecolor: color.rgba(0, 255, 0, 1) });
      if (e != null) ids.push(e);
    } catch(e) {}

    // Test F: horizontalLine nur mit Preis (kein 3. Argument)
    try {
      var f = horizontalLine(t - offMs, cl * 0.997);
      if (f != null) ids.push(f);
    } catch(e) {}

  } catch(err) {}
};
