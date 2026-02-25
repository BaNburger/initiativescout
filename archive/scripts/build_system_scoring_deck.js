#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");

const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const {
  FaChartLine,
  FaDatabase,
  FaCogs,
  FaBalanceScale,
  FaExclamationTriangle,
  FaTasks,
} = require("react-icons/fa");

const { buildSystemScoringSummary } = require("./lib/system_scoring_summary");

const COLORS = {
  bg: "F4F6F8",
  ink: "0B1F33",
  panel: "FFFFFF",
  panelSoft: "E8EEF3",
  primary: "0A4F7A",
  secondary: "1E7D73",
  accent: "E07A1F",
  danger: "B42318",
  muted: "5F6C7B",
  border: "C9D4DF",
};

const SHAPES = {
  LINE: "line",
  RECTANGLE: "rect",
  ROUNDED_RECTANGLE: "roundRect",
  CHEVRON: "chevron",
};

function parseArgs(argv) {
  const repoRoot = path.resolve(__dirname, "..");
  const defaults = {
    exportsDir: path.join(repoRoot, "data", "exports"),
    configDir: path.join(repoRoot, "config"),
    reportsDir: path.join(repoRoot, "reports", "latest"),
    out: "",
    topN: 5,
    language: "en",
  };

  const options = { ...defaults };
  for (let index = 2; index < argv.length; index += 1) {
    const current = argv[index];
    if (!current.startsWith("--")) {
      throw new Error(`Unknown argument: ${current}`);
    }
    const [flag, inlineValue] = current.split("=", 2);
    const nextValue = inlineValue === undefined ? argv[index + 1] : inlineValue;
    const consumesNext = inlineValue === undefined;

    switch (flag) {
      case "--exports-dir":
        options.exportsDir = path.resolve(nextValue);
        if (consumesNext) index += 1;
        break;
      case "--config-dir":
        options.configDir = path.resolve(nextValue);
        if (consumesNext) index += 1;
        break;
      case "--reports-dir":
        options.reportsDir = path.resolve(nextValue);
        if (consumesNext) index += 1;
        break;
      case "--out":
        options.out = path.resolve(nextValue);
        if (consumesNext) index += 1;
        break;
      case "--top-n":
        options.topN = Number.parseInt(nextValue, 10);
        if (consumesNext) index += 1;
        break;
      case "--language":
        options.language = String(nextValue || "").trim().toLowerCase();
        if (consumesNext) index += 1;
        break;
      case "--help":
        printHelp();
        process.exit(0);
      default:
        throw new Error(`Unknown flag: ${flag}`);
    }
  }

  if (!Number.isFinite(options.topN) || options.topN <= 0) {
    throw new Error("--top-n must be a positive integer");
  }
  if (options.language !== "en") {
    throw new Error(`--language must be "en" for this deck (received: "${options.language}")`);
  }
  if (!options.out) {
    options.out = path.join(options.reportsDir, "System_and_Scoring_Deck.pptx");
  }
  return options;
}

function printHelp() {
  console.log(`Usage:
  node scripts/build_system_scoring_deck.js [options]

Options:
  --exports-dir <path>   Path to data exports directory
  --config-dir <path>    Path to config directory
  --reports-dir <path>   Path to reports/latest directory
  --out <path>           Output PPTX path
  --top-n <int>          Top-N rows shown in ranking slides (default: 5)
  --language <en>        Language code (fixed to "en")
  --help                 Show this help
`);
}

function fmt(value, digits = 4) {
  return Number(value).toFixed(digits);
}

function asPct(value) {
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function compactIsoDate(iso) {
  if (!iso) return "n/a";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "n/a";
  return parsed.toISOString().slice(0, 10);
}

function renderIconSvg(IconComponent, color = "#000000", size = 256) {
  return ReactDOMServer.renderToStaticMarkup(
    React.createElement(IconComponent, { color, size: String(size) })
  );
}

async function iconToBase64Png(IconComponent, color, size = 256) {
  const svg = renderIconSvg(IconComponent, color, size);
  const pngBuffer = await sharp(Buffer.from(svg)).png().toBuffer();
  return `image/png;base64,${pngBuffer.toString("base64")}`;
}

function addFooter(slide, footerText) {
  slide.addShape(SHAPES.LINE, {
    x: 0.3,
    y: 5.3,
    w: 9.4,
    h: 0,
    line: { color: COLORS.border, pt: 1 },
  });
  slide.addText(footerText, {
    x: 0.35,
    y: 5.35,
    w: 9.2,
    h: 0.2,
    fontSize: 9,
    fontFace: "Calibri",
    color: COLORS.muted,
    align: "right",
    margin: 0,
  });
}

function addSlideTitle(slide, title, subtitle = "") {
  slide.addText(title, {
    x: 0.35,
    y: 0.2,
    w: 8.8,
    h: 0.45,
    fontSize: 24,
    bold: true,
    color: COLORS.ink,
    fontFace: "Aptos Display",
    margin: 0,
  });
  if (subtitle) {
    slide.addText(subtitle, {
      x: 0.35,
      y: 0.66,
      w: 8.8,
      h: 0.28,
      fontSize: 12,
      color: COLORS.muted,
      fontFace: "Aptos",
      margin: 0,
    });
  }
}

function addPanel(slide, x, y, w, h, color = COLORS.panel) {
  slide.addShape(SHAPES.ROUNDED_RECTANGLE, {
    x,
    y,
    w,
    h,
    rectRadius: 0.04,
    line: { color: COLORS.border, pt: 1 },
    fill: { color },
  });
}

async function buildDeck(options) {
  const summary = buildSystemScoringSummary({
    exportsDir: options.exportsDir,
    configDir: options.configDir,
    topN: options.topN,
  });

  const icons = {
    chart: await iconToBase64Png(FaChartLine, "#0A4F7A"),
    data: await iconToBase64Png(FaDatabase, "#1E7D73"),
    cog: await iconToBase64Png(FaCogs, "#0A4F7A"),
    gates: await iconToBase64Png(FaBalanceScale, "#1E7D73"),
    warning: await iconToBase64Png(FaExclamationTriangle, "#B42318"),
    tasks: await iconToBase64Png(FaTasks, "#0A4F7A"),
  };

  const dataAsOf = compactIsoDate(summary.metadata.data_as_of);
  const generatedOn = compactIsoDate(summary.metadata.generated_at);
  const footer = `Data as of ${dataAsOf} · Generated ${generatedOn}`;

  const presentation = new pptxgen();
  presentation.layout = "LAYOUT_WIDE";
  presentation.author = "UnicornInitiative";
  presentation.company = "UnicornInitiative";
  presentation.subject = "System and scoring explainability";
  presentation.title = "Current System & Scoring Walkthrough";
  presentation.theme = {
    lang: "en-US",
    headFontFace: "Aptos Display",
    bodyFontFace: "Aptos",
  };

  // Slide 1: Title & purpose
  {
    const slide = presentation.addSlide();
    slide.background = { color: COLORS.bg };
    addPanel(slide, 0.3, 0.2, 9.4, 5.0, COLORS.panel);
    slide.addImage({ data: icons.chart, x: 8.9, y: 0.5, w: 0.55, h: 0.55 });
    addSlideTitle(
      slide,
      "Current System & Scoring Walkthrough",
      "Data-driven explainability deck for pipeline logic, scoring mechanics, and current output quality."
    );
    slide.addText(
      [
        { text: "Purpose: ", options: { bold: true } },
        { text: "Make the current system and scoring behavior understandable end-to-end." },
      ],
      { x: 0.7, y: 1.45, w: 8.6, h: 0.35, fontSize: 14, color: COLORS.ink }
    );
    slide.addText(
      [
        { text: "Scope: ", options: { bold: true } },
        { text: "Base scoring, lens rankings, DD model, gate behavior, and visible quality limits." },
      ],
      { x: 0.7, y: 1.95, w: 8.6, h: 0.35, fontSize: 14, color: COLORS.ink }
    );
    slide.addText(
      [
        { text: "Dataset snapshot: ", options: { bold: true } },
        {
          text: `${summary.coverage.initiatives_total} initiatives, ${summary.coverage.legacy_scored_total} base scores, ${summary.coverage.dd_scored_total} DD scores.`,
        },
      ],
      { x: 0.7, y: 2.45, w: 8.6, h: 0.35, fontSize: 14, color: COLORS.ink }
    );
    slide.addText("Read this deck as an explainability artifact, not a marketing document.", {
      x: 0.7,
      y: 3.2,
      w: 8.6,
      h: 0.45,
      fontSize: 16,
      bold: true,
      color: COLORS.primary,
      fontFace: "Aptos Display",
    });
    addFooter(slide, footer);
  }

  // Slide 2: System flow
  {
    const slide = presentation.addSlide();
    slide.background = { color: COLORS.bg };
    addSlideTitle(
      slide,
      "System Flow",
      "Pipeline from ingestion to explainable outputs (`run-all` and `run-dd`)."
    );
    slide.addImage({ data: icons.cog, x: 8.95, y: 0.25, w: 0.5, h: 0.5 });
    const stages = [
      "seed-from-markdown",
      "scrape-directories",
      "enrich-websites",
      "ingest-people",
      "score",
      "rank",
      "export",
    ];
    stages.forEach((stage, index) => {
      const x = 0.5 + index * 1.32;
      addPanel(slide, x, 1.2, 1.2, 0.75, COLORS.panel);
      slide.addText(stage, {
        x: x + 0.08,
        y: 1.47,
        w: 1.05,
        h: 0.2,
        fontSize: 9,
        align: "center",
        color: COLORS.ink,
        bold: true,
      });
      if (index < stages.length - 1) {
        slide.addShape(SHAPES.CHEVRON, {
          x: x + 1.16,
          y: 1.47,
          w: 0.18,
          h: 0.22,
          fill: { color: COLORS.secondary },
          line: { color: COLORS.secondary },
        });
      }
    });
    addPanel(slide, 0.45, 2.4, 9.1, 1.1, COLORS.panel);
    slide.addText("DD extension: collect-github + collect-dd-public + dd-score + dd-gate + dd-rank + dd-report", {
      x: 0.7,
      y: 2.72,
      w: 8.6,
      h: 0.32,
      fontSize: 12,
      color: COLORS.ink,
      bold: true,
    });
    addPanel(slide, 0.45, 3.7, 9.1, 1.35, COLORS.panel);
    slide.addText("Primary artifacts", {
      x: 0.7,
      y: 3.88,
      w: 3.2,
      h: 0.25,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    const artifacts = [
      "data/exports/initiatives_master.json",
      "data/exports/score_explanations.json",
      "data/exports/top_outreach_targets.json",
      "data/exports/top_venture_upside.json",
      "data/exports/dd_scores.json",
      "data/exports/dd_gates.json",
    ];
    artifacts.forEach((item, index) => {
      slide.addText(`• ${item}`, {
        x: index < 3 ? 0.75 : 4.95,
        y: 4.12 + (index % 3) * 0.26,
        w: 4.0,
        h: 0.22,
        fontSize: 10,
        color: COLORS.ink,
      });
    });
    addFooter(slide, footer);
  }

  // Slide 3: Coverage
  {
    const slide = presentation.addSlide();
    slide.background = { color: COLORS.bg };
    addSlideTitle(slide, "Current Data Coverage", "Coverage and distribution in the current exported snapshot.");
    slide.addImage({ data: icons.data, x: 8.95, y: 0.25, w: 0.5, h: 0.5 });
    const metrics = [
      { label: "Total initiatives", value: summary.coverage.initiatives_total },
      { label: "Legacy scoring rows", value: summary.coverage.legacy_scored_total },
      { label: "DD scoring rows", value: summary.coverage.dd_scored_total },
      { label: "Summary coverage", value: asPct(summary.coverage.summary_coverage) },
    ];
    metrics.forEach((metric, index) => {
      const x = 0.55 + index * 2.3;
      addPanel(slide, x, 1.1, 2.12, 1.0, COLORS.panel);
      slide.addText(String(metric.value), {
        x: x + 0.1,
        y: 1.35,
        w: 1.95,
        h: 0.3,
        fontSize: 22,
        bold: true,
        align: "center",
        color: COLORS.primary,
      });
      slide.addText(metric.label, {
        x: x + 0.1,
        y: 1.73,
        w: 1.95,
        h: 0.2,
        fontSize: 10,
        align: "center",
        color: COLORS.muted,
      });
    });
    addPanel(slide, 0.55, 2.4, 9.0, 2.5, COLORS.panel);
    slide.addText("University distribution", {
      x: 0.8,
      y: 2.62,
      w: 3.0,
      h: 0.25,
      fontSize: 13,
      bold: true,
      color: COLORS.primary,
    });
    const topUniversities = summary.coverage.university_distribution.slice(0, 3);
    const maxCount = Math.max(...topUniversities.map((row) => row.count), 1);
    topUniversities.forEach((row, index) => {
      const y = 3.02 + index * 0.55;
      slide.addText(row.university, {
        x: 0.85,
        y,
        w: 1.0,
        h: 0.2,
        fontSize: 11,
        bold: true,
        color: COLORS.ink,
      });
      slide.addShape(SHAPES.RECTANGLE, {
        x: 1.9,
        y: y + 0.03,
        w: 5.6,
        h: 0.16,
        fill: { color: COLORS.panelSoft },
        line: { color: COLORS.panelSoft },
      });
      slide.addShape(SHAPES.RECTANGLE, {
        x: 1.9,
        y: y + 0.03,
        w: (row.count / maxCount) * 5.6,
        h: 0.16,
        fill: { color: COLORS.secondary },
        line: { color: COLORS.secondary },
      });
      slide.addText(`${row.count} (${asPct(row.share)})`, {
        x: 7.65,
        y: y - 0.03,
        w: 1.75,
        h: 0.24,
        fontSize: 10,
        align: "right",
        color: COLORS.ink,
      });
    });
    addFooter(slide, footer);
  }

  // Slide 4: Base scoring
  {
    const slide = presentation.addSlide();
    slide.background = { color: COLORS.bg };
    addSlideTitle(slide, "Base Scoring Model", "Core dimensions, weights, and lens formulations.");
    addPanel(slide, 0.5, 1.0, 4.3, 3.9, COLORS.panel);
    slide.addText("Core dimensions (1-5 scale)", {
      x: 0.75,
      y: 1.22,
      w: 3.8,
      h: 0.25,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    const dimensions = Object.entries(summary.base_scoring.dimension_weights || {});
    dimensions.forEach(([key, weight], index) => {
      slide.addText(`${index + 1}. ${key}`, {
        x: 0.8,
        y: 1.58 + index * 0.38,
        w: 2.6,
        h: 0.23,
        fontSize: 11,
        color: COLORS.ink,
      });
      slide.addText(asPct(weight), {
        x: 3.45,
        y: 1.58 + index * 0.38,
        w: 1.1,
        h: 0.23,
        fontSize: 11,
        bold: true,
        align: "right",
        color: COLORS.secondary,
      });
    });
    slide.addText("Composite formula", {
      x: 0.8,
      y: 3.35,
      w: 2.0,
      h: 0.2,
      fontSize: 10,
      bold: true,
      color: COLORS.primary,
    });
    slide.addText(summary.base_scoring.formula_legacy, {
      x: 0.8,
      y: 3.58,
      w: 3.8,
      h: 0.6,
      fontSize: 9,
      color: COLORS.ink,
      valign: "top",
    });

    addPanel(slide, 5.0, 1.0, 4.5, 3.9, COLORS.panel);
    slide.addText("Lens formulas", {
      x: 5.25,
      y: 1.22,
      w: 3.5,
      h: 0.25,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    slide.addText(`Outreach: ${summary.base_scoring.formula_outreach}`, {
      x: 5.25,
      y: 1.58,
      w: 4.05,
      h: 0.75,
      fontSize: 9,
      color: COLORS.ink,
      valign: "top",
    });
    slide.addText(`Venture upside: ${summary.base_scoring.formula_upside}`, {
      x: 5.25,
      y: 2.28,
      w: 4.05,
      h: 0.75,
      fontSize: 9,
      color: COLORS.ink,
      valign: "top",
    });
    slide.addText("Configured lens weights", {
      x: 5.25,
      y: 3.1,
      w: 3.8,
      h: 0.2,
      fontSize: 10,
      bold: true,
      color: COLORS.primary,
    });
    const outreachWeights = summary.base_scoring.lens_weights?.outreach_now || {};
    const upsideWeights = summary.base_scoring.lens_weights?.venture_upside || {};
    const outreachText = Object.entries(outreachWeights)
      .map(([k, v]) => `${k}: ${asPct(v)}`)
      .join(" | ");
    const upsideText = Object.entries(upsideWeights)
      .map(([k, v]) => `${k}: ${asPct(v)}`)
      .join(" | ");
    slide.addText(`Outreach: ${outreachText}`, {
      x: 5.25,
      y: 3.37,
      w: 4.05,
      h: 0.5,
      fontSize: 8,
      color: COLORS.ink,
      valign: "top",
    });
    slide.addText(`Upside: ${upsideText}`, {
      x: 5.25,
      y: 3.85,
      w: 4.05,
      h: 0.5,
      fontSize: 8,
      color: COLORS.ink,
      valign: "top",
    });
    addFooter(slide, footer);
  }

  // Slide 5: Component mechanics
  {
    const slide = presentation.addSlide();
    slide.background = { color: COLORS.bg };
    addSlideTitle(slide, "Component Mechanics", "How evidence, confidence, and seed bridges influence scores.");
    addPanel(slide, 0.5, 1.0, 9.0, 3.9, COLORS.panel);
    const bullets = [
      "Deterministic, evidence-backed component scoring with explicit weighted contributions.",
      "Evidence quality scales component value and confidence before aggregation.",
      "Core scoring: non-seed components without evidence are set to 0 contribution.",
      `Seed bridge cap: ${Math.round(summary.base_scoring.seed_bridge_cap * 100)}% max influence per core dimension.`,
      `DD no-evidence behavior: ${summary.base_scoring.no_evidence_behavior.dd_scoring}.`,
      "Confidence is dimension-weighted and should be read alongside raw score values.",
    ];
    bullets.forEach((line, index) => {
      slide.addText(`• ${line}`, {
        x: 0.85,
        y: 1.35 + index * 0.45,
        w: 8.4,
        h: 0.32,
        fontSize: 12,
        color: COLORS.ink,
      });
    });
    slide.addText("Practical interpretation: score + confidence + evidence references are the minimum decision trio.", {
      x: 0.85,
      y: 4.22,
      w: 8.4,
      h: 0.28,
      fontSize: 11,
      color: COLORS.secondary,
      bold: true,
    });
    addFooter(slide, footer);
  }

  // Slide 6: Lens outputs
  {
    const slide = presentation.addSlide();
    slide.background = { color: COLORS.bg };
    addSlideTitle(slide, "Lens Outputs (Top 5)", "Outreach-now and venture-upside are different prioritization lenses.");
    addPanel(slide, 0.45, 1.0, 4.45, 3.45, COLORS.panel);
    addPanel(slide, 5.1, 1.0, 4.45, 3.45, COLORS.panel);
    slide.addText("Outreach targets", {
      x: 0.7,
      y: 1.2,
      w: 2.8,
      h: 0.24,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    slide.addText("Venture upside", {
      x: 5.35,
      y: 1.2,
      w: 2.8,
      h: 0.24,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    summary.lens_outputs.outreach_top.slice(0, 5).forEach((row, index) => {
      slide.addText(
        `${index + 1}. ${row.initiative_name} (${fmt(row.score, 3)})  M:${fmt(
          row.market_opportunity || 0,
          2
        )} T:${fmt(row.team_strength || 0, 2)} S:${fmt(row.support_fit || 0, 2)}`,
        {
          x: 0.68,
          y: 1.55 + index * 0.5,
          w: 4.1,
          h: 0.4,
          fontSize: 9,
          color: COLORS.ink,
          valign: "top",
        }
      );
    });
    summary.lens_outputs.upside_top.slice(0, 5).forEach((row, index) => {
      slide.addText(
        `${index + 1}. ${row.initiative_name} (${fmt(row.score, 3)})  T:${fmt(
          row.tech_depth || 0,
          2
        )} M:${fmt(row.market_opportunity || 0, 2)} Team:${fmt(row.team_strength || 0, 2)}`,
        {
          x: 5.33,
          y: 1.55 + index * 0.5,
          w: 4.1,
          h: 0.4,
          fontSize: 9,
          color: COLORS.ink,
          valign: "top",
        }
      );
    });

    addPanel(slide, 0.45, 4.6, 9.1, 0.58, COLORS.panelSoft);
    const contextText = (summary.lens_outputs.outreach_lead_component_context || [])
      .map(
        (item) =>
          `${item.dimension}.${item.component_key}=${fmt(item.weighted_contribution, 3)} (conf ${fmt(item.confidence, 2)})`
      )
      .join(" | ");
    slide.addText(`Example component context (current #1 outreach): ${contextText || "n/a"}`, {
      x: 0.68,
      y: 4.78,
      w: 8.7,
      h: 0.28,
      fontSize: 8.5,
      color: COLORS.ink,
    });
    addFooter(slide, footer);
  }

  // Slide 7: DD scoring model
  {
    const slide = presentation.addSlide();
    slide.background = { color: COLORS.bg };
    addSlideTitle(slide, "DD Scoring Model", "Conviction blends DD dimensions with confidence-aware weighting.");
    addPanel(slide, 0.45, 1.0, 4.45, 3.9, COLORS.panel);
    addPanel(slide, 5.1, 1.0, 4.45, 3.9, COLORS.panel);
    slide.addText("Conviction weights", {
      x: 0.7,
      y: 1.2,
      w: 3.5,
      h: 0.24,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    Object.entries(summary.dd_model.conviction_weights || {}).forEach(([key, weight], index) => {
      slide.addText(`${key}: ${asPct(weight)}`, {
        x: 0.75,
        y: 1.58 + index * 0.34,
        w: 3.8,
        h: 0.22,
        fontSize: 11,
        color: COLORS.ink,
      });
    });
    slide.addText("Current conviction distribution", {
      x: 0.75,
      y: 3.42,
      w: 3.8,
      h: 0.2,
      fontSize: 10,
      bold: true,
      color: COLORS.primary,
    });
    slide.addText(
      `Score min/p50/max: ${fmt(summary.dd_model.score_distribution.conviction_score.min, 2)} / ${fmt(
        summary.dd_model.score_distribution.conviction_score.p50,
        2
      )} / ${fmt(summary.dd_model.score_distribution.conviction_score.max, 2)}`,
      {
        x: 0.75,
        y: 3.65,
        w: 3.9,
        h: 0.35,
        fontSize: 9,
        color: COLORS.ink,
      }
    );
    slide.addText(
      `Confidence min/p50/max: ${fmt(summary.dd_model.score_distribution.conviction_confidence.min, 2)} / ${fmt(
        summary.dd_model.score_distribution.conviction_confidence.p50,
        2
      )} / ${fmt(summary.dd_model.score_distribution.conviction_confidence.max, 2)}`,
      {
        x: 0.75,
        y: 3.93,
        w: 3.9,
        h: 0.35,
        fontSize: 9,
        color: COLORS.ink,
      }
    );

    slide.addText("Component groups", {
      x: 5.35,
      y: 1.2,
      w: 3.5,
      h: 0.24,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    const groups = summary.dd_model.component_weights || {};
    const groupLines = [
      `team_dd: ${Object.keys(groups.team_dd || {}).join(", ")}`,
      `tech_dd: ${Object.keys(groups.tech_dd || {}).join(", ")}`,
      `market_dd: ${Object.keys(groups.market_dd || {}).join(", ")}`,
      `execution_dd: ${Object.keys(groups.execution_dd || {}).join(", ")}`,
      `legal_dd: ${Object.keys(groups.legal_dd || {}).join(", ")}`,
    ];
    groupLines.forEach((line, index) => {
      slide.addText(`• ${line}`, {
        x: 5.35,
        y: 1.57 + index * 0.38,
        w: 3.95,
        h: 0.3,
        fontSize: 9.5,
        color: COLORS.ink,
      });
    });
    slide.addText("Market validation stages", {
      x: 5.35,
      y: 3.55,
      w: 3.6,
      h: 0.2,
      fontSize: 10,
      bold: true,
      color: COLORS.primary,
    });
    const stageText = (summary.dd_model.stage_distribution || [])
      .map((entry) => `${entry.stage} (${entry.count})`)
      .join(" | ");
    slide.addText(stageText, {
      x: 5.35,
      y: 3.8,
      w: 3.95,
      h: 0.6,
      fontSize: 9,
      color: COLORS.ink,
      valign: "top",
    });
    addFooter(slide, footer);
  }

  // Slide 8: DD gates configuration
  {
    const slide = presentation.addSlide();
    slide.background = { color: COLORS.bg };
    addSlideTitle(slide, "DD Gates A–D (Configured Thresholds)", "Gate logic is threshold-driven and evidence-sensitive.");
    slide.addImage({ data: icons.gates, x: 8.95, y: 0.25, w: 0.5, h: 0.5 });
    addPanel(slide, 0.45, 1.0, 9.1, 3.9, COLORS.panel);
    const thresholds = summary.dd_gates.thresholds || {};
    const gateLines = [
      `Gate A: team_dd>=${thresholds.gate_a?.team_dd_min}, team_tech_fit>=${thresholds.gate_a?.team_tech_fit_min}, named_operators>=${thresholds.gate_a?.min_named_operators}, technical_leads>=${thresholds.gate_a?.min_technical_leads}, qualifying_evidence>=${thresholds.gate_a?.min_qualifying_evidence}`,
      `Gate B: tech_dd>=${thresholds.gate_b?.tech_dd_min}, source_classes>=${thresholds.gate_b?.min_source_classes}, qualifying_evidence>=${thresholds.gate_b?.min_qualifying_evidence}, require_hard_proof=${thresholds.gate_b?.require_hard_proof_artifact}`,
      `Gate C: market_dd>=${thresholds.gate_c?.market_dd_min}, min_stage=${thresholds.gate_c?.min_stage}, source_classes>=${thresholds.gate_c?.min_source_classes}, qualifying_evidence>=${thresholds.gate_c?.min_qualifying_evidence}`,
      `Gate D: legal_dd>=${thresholds.gate_d?.legal_dd_min}, require_entity_known=${thresholds.gate_d?.require_entity_known}, require_ip_known=${thresholds.gate_d?.require_ip_known}, max_legal_risk<=${thresholds.gate_d?.max_legal_risk_score}`,
    ];
    gateLines.forEach((line, index) => {
      addPanel(slide, 0.75, 1.35 + index * 0.86, 8.45, 0.72, index % 2 === 0 ? COLORS.panel : COLORS.panelSoft);
      slide.addText(line, {
        x: 0.95,
        y: 1.56 + index * 0.86,
        w: 8.05,
        h: 0.45,
        fontSize: 9.5,
        color: COLORS.ink,
        valign: "top",
      });
    });
    addFooter(slide, footer);
  }

  // Slide 9: Gate results
  {
    const slide = presentation.addSlide();
    slide.background = { color: COLORS.bg };
    addSlideTitle(slide, "Current Gate Results", "Pass rates and dominant blockers in the current dataset.");
    addPanel(slide, 0.45, 1.0, 4.35, 3.9, COLORS.panel);
    addPanel(slide, 4.95, 1.0, 4.6, 3.9, COLORS.panel);

    slide.addText("Pass rates", {
      x: 0.72,
      y: 1.2,
      w: 2.0,
      h: 0.22,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    CORE_GATES.forEach((gate, index) => {
      const stats = summary.dd_gates.pass_rates[gate];
      const y = 1.6 + index * 0.72;
      slide.addText(`Gate ${gate}`, {
        x: 0.72,
        y,
        w: 0.9,
        h: 0.2,
        fontSize: 11,
        bold: true,
        color: COLORS.ink,
      });
      slide.addShape(SHAPES.RECTANGLE, {
        x: 1.7,
        y: y + 0.03,
        w: 2.5,
        h: 0.16,
        fill: { color: COLORS.panelSoft },
        line: { color: COLORS.panelSoft },
      });
      slide.addShape(SHAPES.RECTANGLE, {
        x: 1.7,
        y: y + 0.03,
        w: 2.5 * stats.pass_rate,
        h: 0.16,
        fill: { color: stats.pass_rate > 0 ? COLORS.secondary : COLORS.danger },
        line: { color: stats.pass_rate > 0 ? COLORS.secondary : COLORS.danger },
      });
      slide.addText(`${asPct(stats.pass_rate)} (${stats.pass}/${stats.total})`, {
        x: 1.7,
        y: y + 0.24,
        w: 2.55,
        h: 0.2,
        fontSize: 9,
        align: "right",
        color: COLORS.muted,
      });
    });

    slide.addText("Top blockers by gate", {
      x: 5.25,
      y: 1.2,
      w: 3.2,
      h: 0.22,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    CORE_GATES.forEach((gate, gateIndex) => {
      const blocker = summary.dd_gates.blocker_frequencies[gate][0];
      const text = blocker ? `${blocker.blocker} (${blocker.count})` : "none";
      slide.addText(`Gate ${gate}: ${text}`, {
        x: 5.25,
        y: 1.57 + gateIndex * 0.34,
        w: 4.1,
        h: 0.24,
        fontSize: 9.5,
        color: COLORS.ink,
      });
    });
    const allZero = summary.dd_gates.all_gates_zero_pass;
    slide.addText(
      allZero
        ? "Current state: all gates are at 0% pass rate. Interpret DD outputs primarily as blocker diagnostics."
        : "Some gates currently pass; use blockers to prioritize remediation.",
      {
        x: 5.25,
        y: 3.35,
        w: 4.1,
        h: 0.8,
        fontSize: 10,
        bold: true,
        color: allZero ? COLORS.danger : COLORS.secondary,
        valign: "top",
      }
    );
    addFooter(slide, footer);
  }

  // Slide 10: Quality signals
  {
    const slide = presentation.addSlide();
    slide.background = { color: COLORS.bg };
    addSlideTitle(slide, "Observed Quality Signals", "Current output artifacts that require cautious interpretation.");
    slide.addImage({ data: icons.warning, x: 8.95, y: 0.25, w: 0.5, h: 0.5 });
    addPanel(slide, 0.45, 1.0, 9.1, 3.9, COLORS.panel);
    slide.addText("Examples of ranking noise in top operators", {
      x: 0.72,
      y: 1.2,
      w: 4.5,
      h: 0.22,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    (summary.quality_signals.operator_noise_examples || []).slice(0, 6).forEach((example, index) => {
      slide.addText(
        `${index + 1}. ${example.name} (score ${fmt(example.score, 2)}, keyword "${example.matched_keyword}")`,
        {
          x: 0.75,
          y: 1.55 + index * 0.33,
          w: 8.5,
          h: 0.22,
          fontSize: 10,
          color: COLORS.ink,
        }
      );
    });
    slide.addText("Interpretation guidance", {
      x: 0.72,
      y: 3.72,
      w: 3.0,
      h: 0.2,
      fontSize: 10,
      bold: true,
      color: COLORS.primary,
    });
    (summary.quality_signals.notes || []).forEach((note, index) => {
      slide.addText(`• ${note}`, {
        x: 0.75,
        y: 3.94 + index * 0.27,
        w: 8.5,
        h: 0.22,
        fontSize: 9.2,
        color: COLORS.ink,
      });
    });
    addFooter(slide, footer);
  }

  // Slide 11: How to use
  {
    const slide = presentation.addSlide();
    slide.background = { color: COLORS.bg };
    addSlideTitle(slide, "How to Read & Use the Scores", "Practical workflow for decision support with current system constraints.");
    slide.addImage({ data: icons.tasks, x: 8.95, y: 0.25, w: 0.5, h: 0.5 });
    addPanel(slide, 0.45, 1.0, 4.5, 3.9, COLORS.panel);
    addPanel(slide, 5.05, 1.0, 4.5, 3.9, COLORS.panel);
    slide.addText("Recommended reading order", {
      x: 0.72,
      y: 1.2,
      w: 3.5,
      h: 0.22,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    const steps = [
      "1. Start with outreach/upside top lists.",
      "2. Open score explanations for component-level evidence.",
      "3. Validate confidence before prioritizing action.",
      "4. Use DD gates to identify concrete blockers.",
      "5. Treat zero-pass gate states as a calibration signal.",
    ];
    steps.forEach((step, index) => {
      slide.addText(step, {
        x: 0.76,
        y: 1.54 + index * 0.42,
        w: 4.1,
        h: 0.27,
        fontSize: 10.5,
        color: COLORS.ink,
      });
    });
    slide.addText("Operational caveats", {
      x: 5.33,
      y: 1.2,
      w: 3.2,
      h: 0.22,
      fontSize: 12,
      bold: true,
      color: COLORS.primary,
    });
    const caveats = [
      "Current DD gate pass rates are 0% across A/B/C/D.",
      "Operator ranking currently contains extraction noise.",
      "Use rankings as triage signals, not standalone decisions.",
      "Calibration and source-quality improvements should precede high-stakes use.",
    ];
    caveats.forEach((line, index) => {
      slide.addText(`• ${line}`, {
        x: 5.33,
        y: 1.54 + index * 0.42,
        w: 3.95,
        h: 0.3,
        fontSize: 10,
        color: COLORS.ink,
      });
    });
    slide.addText(`Build command: node scripts/build_system_scoring_deck.js --out ${options.out}`, {
      x: 5.33,
      y: 3.82,
      w: 3.95,
      h: 0.55,
      fontSize: 8.5,
      color: COLORS.muted,
      valign: "top",
    });
    addFooter(slide, footer);
  }

  if (presentation._slides.length !== 11) {
    throw new Error(`Deck integrity check failed: expected 11 slides, got ${presentation._slides.length}`);
  }

  fs.mkdirSync(path.dirname(options.out), { recursive: true });
  await presentation.writeFile({ fileName: options.out });
  return { outputPath: options.out, slideCount: presentation._slides.length, summary };
}

const CORE_GATES = ["A", "B", "C", "D"];

async function main() {
  try {
    const options = parseArgs(process.argv);
    const result = await buildDeck(options);
    console.log(`Deck generated: ${result.outputPath}`);
    console.log(`Slides: ${result.slideCount}`);
  } catch (error) {
    console.error(`[build-system-scoring-deck] ${error.message}`);
    process.exitCode = 1;
  }
}

main();
