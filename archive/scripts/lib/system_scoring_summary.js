const fs = require("node:fs");
const path = require("node:path");
const YAML = require("yaml");

const CORE_GATES = ["A", "B", "C", "D"];

const REQUIRED_EXPORT_FILES = [
  "initiatives_master.json",
  "top_outreach_targets.json",
  "top_venture_upside.json",
  "score_explanations.json",
  "dd_scores.json",
  "dd_gates.json",
  "top_talent_operators.json",
];

const REQUIRED_CONFIG_FILES = [
  "scoring_weights.yaml",
  "dd_rubric.yaml",
  "dd_gate_thresholds.yaml",
];

function safeNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function round(value, digits = 4) {
  const factor = 10 ** digits;
  return Math.round(safeNumber(value) * factor) / factor;
}

function median(values) {
  if (!values.length) {
    return 0;
  }
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 0) {
    return (sorted[mid - 1] + sorted[mid]) / 2;
  }
  return sorted[mid];
}

function resolveRequiredFiles({ exportsDir, configDir }) {
  const exportFiles = REQUIRED_EXPORT_FILES.map((fileName) => ({
    kind: "export",
    fileName,
    fullPath: path.join(exportsDir, fileName),
  }));
  const configFiles = REQUIRED_CONFIG_FILES.map((fileName) => ({
    kind: "config",
    fileName,
    fullPath: path.join(configDir, fileName),
  }));
  return [...exportFiles, ...configFiles];
}

function assertRequiredFiles(requiredFiles) {
  const missing = requiredFiles.filter((entry) => !fs.existsSync(entry.fullPath));
  if (missing.length === 0) {
    return;
  }
  const lines = missing.map((entry) => `- ${entry.kind}/${entry.fileName}: ${entry.fullPath}`);
  throw new Error(`[DataLoader] Missing required files:\n${lines.join("\n")}`);
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    throw new Error(`[DataLoader] Failed to parse JSON ${filePath}: ${error.message}`);
  }
}

function readYaml(filePath) {
  try {
    return YAML.parse(fs.readFileSync(filePath, "utf8")) || {};
  } catch (error) {
    throw new Error(`[DataLoader] Failed to parse YAML ${filePath}: ${error.message}`);
  }
}

function parseGateBlockers(reason) {
  if (!reason || typeof reason !== "string") {
    return [];
  }
  const markerIndex = reason.toLowerCase().indexOf("failed:");
  if (markerIndex === -1) {
    return [];
  }
  return reason
    .slice(markerIndex + "failed:".length)
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function universityDistribution(initiatives) {
  const counts = new Map();
  for (const row of initiatives) {
    const university = (row.university || "unknown").toString().trim() || "unknown";
    counts.set(university, (counts.get(university) || 0) + 1);
  }
  const total = initiatives.length || 1;
  return [...counts.entries()]
    .map(([university, count]) => ({
      university,
      count,
      share: round(count / total, 4),
    }))
    .sort((a, b) => b.count - a.count);
}

function topLensRows(rows, topN, fieldSet) {
  return rows.slice(0, topN).map((row, index) => {
    const name = row.initiative_name || row.item_name || row.name || `Item ${index + 1}`;
    const base = {
      rank: safeNumber(row.rank, index + 1),
      initiative_name: String(name),
      initiative_id: Number.isFinite(Number(row.initiative_id)) ? Number(row.initiative_id) : null,
      score: round(row.score),
    };
    for (const field of fieldSet) {
      if (row[field] !== undefined && row[field] !== null) {
        base[field] = round(row[field]);
      }
    }
    return base;
  });
}

function explanationContext(scoreExplanations, initiativeId, topN = 3) {
  const match = scoreExplanations.find((row) => safeNumber(row.initiative_id, -1) === safeNumber(initiativeId, -2));
  if (!match || !Array.isArray(match.components)) {
    return [];
  }
  return [...match.components]
    .sort((a, b) => safeNumber(b.weighted_contribution) - safeNumber(a.weighted_contribution))
    .slice(0, topN)
    .map((component) => ({
      dimension: String(component.dimension || "unknown"),
      component_key: String(component.component_key || "unknown"),
      weighted_contribution: round(component.weighted_contribution),
      confidence: round(component.confidence),
      provenance: String(component.provenance || "derived"),
    }));
}

function gatePassRates(ddGates) {
  const stats = {};
  for (const gate of CORE_GATES) {
    const rows = ddGates.filter((row) => String(row.gate_name || "").toUpperCase() === gate);
    const pass = rows.filter((row) => String(row.status || "").toLowerCase() === "pass").length;
    const total = rows.length;
    stats[gate] = {
      pass,
      total,
      pass_rate: total > 0 ? round(pass / total, 4) : 0,
    };
  }
  return stats;
}

function gateBlockers(ddGates, limit = 5) {
  const counters = {
    A: new Map(),
    B: new Map(),
    C: new Map(),
    D: new Map(),
  };
  for (const row of ddGates) {
    const gate = String(row.gate_name || "").toUpperCase();
    if (!CORE_GATES.includes(gate)) {
      continue;
    }
    if (String(row.status || "").toLowerCase() !== "fail") {
      continue;
    }
    const blockers = parseGateBlockers(String(row.reason || ""));
    for (const blocker of blockers) {
      counters[gate].set(blocker, (counters[gate].get(blocker) || 0) + 1);
    }
  }

  const summary = {};
  for (const gate of CORE_GATES) {
    summary[gate] = [...counters[gate].entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, limit)
      .map(([blocker, count]) => ({ blocker, count }));
  }
  return summary;
}

function fieldDistribution(rows, field) {
  const values = rows
    .map((row) => safeNumber(row[field], Number.NaN))
    .filter((value) => Number.isFinite(value));
  if (!values.length) {
    return { min: 0, p50: 0, max: 0 };
  }
  return {
    min: round(Math.min(...values)),
    p50: round(median(values)),
    max: round(Math.max(...values)),
  };
}

function stageDistribution(ddScores) {
  const counts = new Map();
  for (const row of ddScores) {
    const key = String(row.market_validation_stage || "unknown");
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return [...counts.entries()]
    .map(([stage, count]) => ({ stage, count }))
    .sort((a, b) => b.count - a.count);
}

function operatorNoiseExamples(topOperators, limit = 6) {
  const keywords = [
    "privacy",
    "policy",
    "about",
    "contact",
    "social media",
    "facebook",
    "instagram",
    "rights",
    "students",
    "technical university",
  ];

  const examples = [];
  for (const row of topOperators) {
    const name = String(row.person_name || row.name || "");
    const lower = name.toLowerCase();
    const matched = keywords.find((keyword) => lower.includes(keyword));
    if (!matched) {
      continue;
    }
    examples.push({
      rank: safeNumber(row.rank, examples.length + 1),
      name,
      score: round(row.score),
      matched_keyword: matched,
    });
    if (examples.length >= limit) {
      break;
    }
  }
  return examples;
}

function latestMtimeIso(requiredFiles) {
  const mtimes = requiredFiles.map((entry) => fs.statSync(entry.fullPath).mtimeMs);
  const latest = Math.max(...mtimes);
  return new Date(latest).toISOString();
}

function buildSystemScoringSummary({ exportsDir, configDir, topN = 5 }) {
  const normalizedTopN = Math.max(1, Math.floor(safeNumber(topN, 5)));
  const requiredFiles = resolveRequiredFiles({ exportsDir, configDir });
  assertRequiredFiles(requiredFiles);

  const byName = Object.fromEntries(requiredFiles.map((entry) => [entry.fileName, entry.fullPath]));

  const initiativesMaster = readJson(byName["initiatives_master.json"]);
  const topOutreach = readJson(byName["top_outreach_targets.json"]);
  const topUpside = readJson(byName["top_venture_upside.json"]);
  const scoreExplanations = readJson(byName["score_explanations.json"]);
  const ddScores = readJson(byName["dd_scores.json"]);
  const ddGates = readJson(byName["dd_gates.json"]);
  const topTalentOperators = readJson(byName["top_talent_operators.json"]);

  const scoringWeights = readYaml(byName["scoring_weights.yaml"]);
  const ddRubric = readYaml(byName["dd_rubric.yaml"]);
  const ddGateThresholds = readYaml(byName["dd_gate_thresholds.yaml"]);

  const legacyScored = initiativesMaster.filter((row) => {
    const score = row && row.scores ? row.scores.outreach_now_score : null;
    return Number.isFinite(Number(score));
  }).length;

  const universities = universityDistribution(initiativesMaster);
  const outreachTop = topLensRows(topOutreach, normalizedTopN, ["market_opportunity", "support_fit", "team_strength"]);
  const upsideTop = topLensRows(topUpside, normalizedTopN, ["tech_depth", "market_opportunity", "team_strength"]);

  const outreachLead = outreachTop[0] || null;
  const outreachLeadContext = outreachLead ? explanationContext(scoreExplanations, outreachLead.initiative_id || outreachLead.item_key) : [];

  const passRates = gatePassRates(ddGates);
  const blockers = gateBlockers(ddGates, 5);
  const allGatesZeroPass = CORE_GATES.every((gate) => passRates[gate].pass === 0);

  const ddStats = {
    conviction_score: fieldDistribution(ddScores, "conviction_score"),
    conviction_confidence: fieldDistribution(ddScores, "conviction_confidence"),
    team_dd: fieldDistribution(ddScores, "team_dd"),
    tech_dd: fieldDistribution(ddScores, "tech_dd"),
    market_dd: fieldDistribution(ddScores, "market_dd"),
    execution_dd: fieldDistribution(ddScores, "execution_dd"),
    legal_dd: fieldDistribution(ddScores, "legal_dd"),
  };

  const operatorNoise = operatorNoiseExamples(topTalentOperators, 6);

  const notes = [];
  if (allGatesZeroPass) {
    notes.push("All DD gates currently show a 0% pass rate. Treat DD output as diagnostic, not investability proof.");
  }
  if (operatorNoise.length > 0) {
    notes.push("Operator ranking currently includes navigation/legal page terms, indicating extraction noise.");
  }
  notes.push("Use score explanations and confidence jointly before taking ranking-driven actions.");

  return {
    metadata: {
      generated_at: new Date().toISOString(),
      data_as_of: latestMtimeIso(requiredFiles),
      top_n: normalizedTopN,
      exports_dir: exportsDir,
      config_dir: configDir,
    },
    coverage: {
      initiatives_total: initiativesMaster.length,
      legacy_scored_total: legacyScored,
      dd_scored_total: ddScores.length,
      summary_coverage: round(initiativesMaster.filter((row) => String(row.description_summary_en || "").trim()).length / Math.max(1, initiativesMaster.length), 4),
      university_distribution: universities,
    },
    base_scoring: {
      dimension_weights: scoringWeights.dimension_weights || {},
      lens_weights: scoringWeights.lens_weights || {},
      formula_legacy: "Composite = tech_depth*0.30 + team_strength*0.25 + market_opportunity*0.25 + maturity*0.20",
      formula_outreach: "Outreach = 0.30*actionability + 0.20*team + 0.20*market + 0.15*tech + 0.15*support_fit",
      formula_upside: "Upside = 0.35*tech + 0.25*market + 0.20*team + 0.10*maturity + 0.10*support_fit",
      seed_bridge_cap: 0.4,
      no_evidence_behavior: {
        core_scoring: "non-seed components without evidence contribute zero",
        dd_scoring: `no-evidence floor=${safeNumber(ddRubric.no_evidence_floor, 1.0)} with confidence penalty=${safeNumber(
          ddRubric.no_evidence_confidence_penalty,
          0.12
        )}`,
      },
    },
    lens_outputs: {
      outreach_top: outreachTop,
      upside_top: upsideTop,
      outreach_lead_component_context: outreachLeadContext,
    },
    dd_model: {
      conviction_weights: ddRubric.conviction_weights || {},
      component_weights: {
        team_dd: ddRubric.team_dd ? ddRubric.team_dd.components || {} : {},
        tech_dd: ddRubric.tech_dd ? ddRubric.tech_dd.components || {} : {},
        market_dd: ddRubric.market_dd ? ddRubric.market_dd.components || {} : {},
        execution_dd: ddRubric.execution_dd ? ddRubric.execution_dd.components || {} : {},
        legal_dd: ddRubric.legal_dd ? ddRubric.legal_dd.components || {} : {},
      },
      validation_stage_scores: ddRubric.validation_stage_scores || {},
      score_distribution: ddStats,
      stage_distribution: stageDistribution(ddScores),
    },
    dd_gates: {
      thresholds: ddGateThresholds,
      pass_rates: passRates,
      blocker_frequencies: blockers,
      all_gates_zero_pass: allGatesZeroPass,
    },
    quality_signals: {
      operator_noise_examples: operatorNoise,
      notes,
    },
  };
}

module.exports = {
  REQUIRED_EXPORT_FILES,
  REQUIRED_CONFIG_FILES,
  resolveRequiredFiles,
  assertRequiredFiles,
  parseGateBlockers,
  buildSystemScoringSummary,
};
