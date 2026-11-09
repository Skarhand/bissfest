// Bissfest-Widget für iPhone und iPad, läuft in der kostenlosen App „Scriptable“.
// Parameter des Widgets: calories, water, coffee oder tea. Ohne Parameter zeigt das kleine Widget die Kalorien, die größeren mehr.
// Dieses Skript enthält deinen persönlichen Zugang zu Bissfest, bitte nicht weitergeben.
// Widerrufen lässt er sich in Bissfest unter Einstellungen → Extras → Home Assistant.

const SERVER = "__SERVER__";
const TOKEN = "__TOKEN__";
const CAFFEINE_GUIDE_MG = 400;

const COLORS = {
  background: Color.dynamic(new Color("#ffffff"), new Color("#18201c")),
  text: Color.dynamic(new Color("#17201b"), new Color("#e8eeea")),
  muted: Color.dynamic(new Color("#66736b"), new Color("#94a39a")),
  track: Color.dynamic(new Color("#e2e7e4"), new Color("#2a352f")),
  accent: Color.dynamic(new Color("#2e8b5f"), new Color("#45b07c")),
};

const litres = (ml) => (ml / 1000).toFixed(1).replace(".", ",");

function count(n, one, many) {
  return n > 0 ? `${n} ${n === 1 ? one : many}` : null;
}

function drinkSummary(d) {
  const parts = [count(d.cups, "Tasse", "Tassen"), count(d.mugs, "Pott", "Pötte"), count(d.espressos, "Espresso", "Espressi")];
  return parts.filter(Boolean).join(" · ") || "Heute noch nichts";
}

function describe(kind, status) {
  if (kind === "calories") {
    const { eaten, remaining } = status.calories, budget = eaten + remaining; // Ziel plus mit Sport verdiente Kalorien
    const number = (n) => n.toLocaleString("de-DE");
    return { title: "🔥 Kalorien", value: `${number(Math.abs(remaining))} kcal`,
             sub: `${remaining < 0 ? "zu viel" : "übrig"}, ${number(eaten)} von ${number(budget)} gegessen`,
             percent: budget > 0 ? Math.round(eaten * 100 / budget) : 100 };
  }
  if (kind === "water") {
    return { title: "💧 Wasser", value: `${litres(status.water.ml)} / ${litres(status.water.goal_ml)} l`,
             sub: `${status.water.percent} % vom Tagesziel`, percent: status.water.percent };
  }
  const drink = status[kind];
  return { title: kind === "coffee" ? "☕ Kaffee" : "🍵 Tee", value: `${drink.caffeine_mg} mg`, sub: drinkSummary(drink),
           percent: kind === "coffee" ? Math.round(drink.caffeine_mg * 100 / CAFFEINE_GUIDE_MG) : null };
}

function progressImage(percent, width) {
  const height = 6, ctx = new DrawContext();
  ctx.size = new Size(width, height);
  ctx.opaque = false;
  ctx.respectScreenScale = true;
  const bar = (w, color) => {
    const path = new Path();
    path.addRoundedRect(new Rect(0, 0, w, height), 3, 3);
    ctx.addPath(path);
    ctx.setFillColor(color);
    ctx.fillPath();
  };
  bar(width, COLORS.track);
  const filled = Math.round(width * Math.min(Math.max(percent, 0), 100) / 100);
  if (filled > 0) bar(Math.max(filled, height), COLORS.accent);
  return ctx.getImage();
}

function addLine(stack, text, size, color, bold) {
  const line = stack.addText(text);
  line.font = bold ? Font.semiboldSystemFont(size) : Font.systemFont(size);
  line.textColor = color;
  line.lineLimit = 1;
  line.minimumScaleFactor = 0.7;
}

function addBlock(parent, info, barWidth) {
  const block = parent.addStack();
  block.layoutVertically();
  addLine(block, info.title, 13, COLORS.muted, false);
  block.addSpacer(4);
  addLine(block, info.value, 20, COLORS.text, true);
  block.addSpacer(6);
  if (info.percent !== null) {
    const bar = block.addImage(progressImage(info.percent, barWidth));
    bar.imageSize = new Size(barWidth, 6);
    block.addSpacer(6);
  }
  addLine(block, info.sub, 11, COLORS.muted, false);
}

function selectKinds(status, family, parameter) {
  const wanted = (parameter || "").trim().toLowerCase();
  const enabled = (kind) => kind === "calories" || kind === "water" || status[kind].enabled;
  if (["calories", "water", "coffee", "tea"].includes(wanted)) return enabled(wanted) ? [wanted] : [];
  if (family === "small") return ["calories"];
  return ["calories", "water", "coffee", "tea"].filter(enabled).slice(0, 3);
}

function message(widget, text) {
  addLine(widget, "Bissfest", 13, COLORS.muted, false);
  widget.addSpacer(6);
  const line = widget.addText(text);
  line.font = Font.systemFont(13);
  line.textColor = COLORS.text;
}

async function loadStatus() {
  const request = new Request(`${SERVER}/api/v1/status`);
  request.headers = { Authorization: `Bearer ${TOKEN}` };
  const status = await request.loadJSON();
  if (request.response.statusCode === 401) throw new Error("Zugang abgelaufen, bitte das Skript in Bissfest neu erzeugen.");
  if (request.response.statusCode !== 200) throw new Error("Bissfest antwortet gerade nicht.");
  return status;
}

async function build(family, parameter) {
  const widget = new ListWidget();
  widget.backgroundColor = COLORS.background;
  widget.setPadding(14, 14, 14, 14);
  widget.url = SERVER;
  widget.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
  try {
    const status = await loadStatus();
    const kinds = selectKinds(status, family, parameter);
    if (!kinds.length) {
      message(widget, "Dieses Getränk ist in Bissfest nicht eingeschaltet.");
    } else if (family === "small" || kinds.length === 1) {
      addBlock(widget, describe(kinds[0], status), family === "small" ? 120 : 280);
    } else {
      const row = widget.addStack();
      kinds.forEach((kind, i) => {
        if (i) row.addSpacer(12);
        addBlock(row, describe(kind, status), kinds.length === 3 ? 84 : 134);
      });
    }
  } catch (err) {
    message(widget, err.message || "Keine Verbindung");
  }
  return widget;
}

const widget = await build(config.widgetFamily || "medium", args.widgetParameter);
if (config.runsInWidget) Script.setWidget(widget);
else await widget.presentMedium();
Script.complete();
