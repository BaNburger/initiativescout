const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const { FaRocket, FaUsers, FaBrain, FaHandshake, FaChartLine, FaUniversity, FaTrophy, FaLaptopCode, FaIndustry, FaCheck } = require("react-icons/fa");

// Color Palette - Deep Tech/Innovation theme
const COLORS = {
  primary: "1E3A5F",      // Deep navy
  secondary: "0891B2",    // Teal accent
  accent: "F59E0B",       // Amber/gold
  light: "F8FAFC",        // Off-white
  dark: "0F172A",         // Near black
  muted: "64748B",        // Gray
  success: "10B981",      // Green
  white: "FFFFFF",
  cardBg: "FFFFFF"
};

// Icon helper
function renderIconSvg(IconComponent, color = "#000000", size = 256) {
  return ReactDOMServer.renderToStaticMarkup(
    React.createElement(IconComponent, { color, size: String(size) })
  );
}

async function iconToBase64Png(IconComponent, color, size = 256) {
  const svg = renderIconSvg(IconComponent, color, size);
  const pngBuffer = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + pngBuffer.toString("base64");
}

async function createPresentation() {
  let pres = new pptxgen();
  pres.layout = "LAYOUT_16x9";
  pres.author = "UnicornInitiative";
  pres.title = "Munich Student Initiatives - Deep Tech Startup Pipeline";

  // Pre-generate icons
  const icons = {
    rocket: await iconToBase64Png(FaRocket, "#" + COLORS.white, 256),
    rocketDark: await iconToBase64Png(FaRocket, "#" + COLORS.secondary, 256),
    users: await iconToBase64Png(FaUsers, "#" + COLORS.secondary, 256),
    brain: await iconToBase64Png(FaBrain, "#" + COLORS.secondary, 256),
    handshake: await iconToBase64Png(FaHandshake, "#" + COLORS.secondary, 256),
    chart: await iconToBase64Png(FaChartLine, "#" + COLORS.secondary, 256),
    university: await iconToBase64Png(FaUniversity, "#" + COLORS.secondary, 256),
    trophy: await iconToBase64Png(FaTrophy, "#" + COLORS.accent, 256),
    laptop: await iconToBase64Png(FaLaptopCode, "#" + COLORS.secondary, 256),
    industry: await iconToBase64Png(FaIndustry, "#" + COLORS.secondary, 256),
    check: await iconToBase64Png(FaCheck, "#" + COLORS.success, 256),
  };

  // ============================================
  // SLIDE 1: Title Slide
  // ============================================
  let slide1 = pres.addSlide();
  slide1.background = { color: COLORS.primary };

  // Accent bar at top
  slide1.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: 10, h: 0.15,
    fill: { color: COLORS.secondary }
  });

  // Icon
  slide1.addImage({ data: icons.rocket, x: 4.5, y: 1.2, w: 1, h: 1 });

  // Title
  slide1.addText("Munich Student Initiatives", {
    x: 0.5, y: 2.4, w: 9, h: 0.8,
    fontSize: 40, fontFace: "Georgia", color: COLORS.white,
    bold: true, align: "center", margin: 0
  });

  // Subtitle
  slide1.addText("Unlocking Europe's Deepest Tech Talent Pipeline", {
    x: 0.5, y: 3.2, w: 9, h: 0.5,
    fontSize: 22, fontFace: "Calibri", color: COLORS.secondary,
    align: "center", margin: 0
  });

  // Tagline
  slide1.addText("A Partnership Opportunity for Compute Providers & Angel Investors", {
    x: 0.5, y: 4.3, w: 9, h: 0.4,
    fontSize: 14, fontFace: "Calibri", color: COLORS.muted,
    align: "center", italic: true, margin: 0
  });

  // Footer
  slide1.addText("UnicornInitiative | February 2026", {
    x: 0.5, y: 5.2, w: 9, h: 0.3,
    fontSize: 11, fontFace: "Calibri", color: COLORS.muted,
    align: "center", margin: 0
  });

  // ============================================
  // SLIDE 2: The Opportunity
  // ============================================
  let slide2 = pres.addSlide();
  slide2.background = { color: COLORS.light };

  // Title
  slide2.addText("The Opportunity", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 32, fontFace: "Georgia", color: COLORS.primary,
    bold: true, margin: 0
  });

  // Left content - Problem
  slide2.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 1.1, w: 4.4, h: 2.0,
    fill: { color: COLORS.white },
    shadow: { type: "outer", blur: 3, offset: 2, angle: 45, opacity: 0.15 }
  });
  slide2.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 1.1, w: 0.08, h: 2.0,
    fill: { color: COLORS.accent }
  });
  slide2.addText("The Challenge", {
    x: 0.75, y: 1.2, w: 4, h: 0.4,
    fontSize: 16, fontFace: "Calibri", color: COLORS.primary,
    bold: true, margin: 0
  });
  slide2.addText([
    { text: "Deep tech startups need years to develop", options: { bullet: true, breakLine: true } },
    { text: "Technical teams lack business support", options: { bullet: true, breakLine: true } },
    { text: "Compute costs block AI innovation", options: { bullet: true } }
  ], {
    x: 0.75, y: 1.65, w: 4, h: 1.3,
    fontSize: 13, fontFace: "Calibri", color: COLORS.dark, margin: 0
  });

  // Right content - Solution
  slide2.addShape(pres.shapes.RECTANGLE, {
    x: 5.1, y: 1.1, w: 4.4, h: 2.0,
    fill: { color: COLORS.white },
    shadow: { type: "outer", blur: 3, offset: 2, angle: 45, opacity: 0.15 }
  });
  slide2.addShape(pres.shapes.RECTANGLE, {
    x: 5.1, y: 1.1, w: 0.08, h: 2.0,
    fill: { color: COLORS.secondary }
  });
  slide2.addText("Our Thesis", {
    x: 5.35, y: 1.2, w: 4, h: 0.4,
    fontSize: 16, fontFace: "Calibri", color: COLORS.primary,
    bold: true, margin: 0
  });
  slide2.addText([
    { text: "Student initiatives = pre-built teams", options: { bullet: true, breakLine: true } },
    { text: "Years of deep tech experience", options: { bullet: true, breakLine: true } },
    { text: "Support at spinout unlocks value", options: { bullet: true } }
  ], {
    x: 5.35, y: 1.65, w: 4, h: 1.3,
    fontSize: 13, fontFace: "Calibri", color: COLORS.dark, margin: 0
  });

  // Big stat section
  slide2.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 3.3, w: 9, h: 2.0,
    fill: { color: COLORS.primary }
  });

  // Stats row
  const stats = [
    { num: "25+", label: "High-Potential\nInitiatives" },
    { num: "700+", label: "TUM Spinoffs\nSince 1990" },
    { num: "€5B+", label: "Alumni Company\nValue Created" },
    { num: "1,100+", label: "Teams Supported\nAnnually" }
  ];

  stats.forEach((stat, i) => {
    const x = 0.7 + (i * 2.3);
    slide2.addText(stat.num, {
      x: x, y: 3.5, w: 2, h: 0.7,
      fontSize: 36, fontFace: "Georgia", color: COLORS.accent,
      bold: true, align: "center", margin: 0
    });
    slide2.addText(stat.label, {
      x: x, y: 4.2, w: 2, h: 0.8,
      fontSize: 11, fontFace: "Calibri", color: COLORS.white,
      align: "center", margin: 0
    });
  });

  // ============================================
  // SLIDE 3: The Ecosystem
  // ============================================
  let slide3 = pres.addSlide();
  slide3.background = { color: COLORS.light };

  slide3.addText("Munich: Europe's Deep Tech Hub", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 32, fontFace: "Georgia", color: COLORS.primary,
    bold: true, margin: 0
  });

  // Three university cards
  const universities = [
    { name: "TUM", full: "Technical University of Munich", count: "13", highlight: "Autonomous driving, Space, AI" },
    { name: "LMU", full: "Ludwig Maximilian University", count: "6", highlight: "CompVis (Stable Diffusion), Biotech" },
    { name: "HM", full: "Hochschule München", count: "6", highlight: "#1 Startup Radar 2025" }
  ];

  universities.forEach((uni, i) => {
    const x = 0.5 + (i * 3.1);
    slide3.addShape(pres.shapes.RECTANGLE, {
      x: x, y: 1.1, w: 2.9, h: 2.4,
      fill: { color: COLORS.white },
      shadow: { type: "outer", blur: 3, offset: 2, angle: 45, opacity: 0.15 }
    });
    slide3.addImage({ data: icons.university, x: x + 1.1, y: 1.25, w: 0.6, h: 0.6 });
    slide3.addText(uni.name, {
      x: x + 0.1, y: 1.9, w: 2.7, h: 0.4,
      fontSize: 20, fontFace: "Georgia", color: COLORS.primary,
      bold: true, align: "center", margin: 0
    });
    slide3.addText(uni.full, {
      x: x + 0.1, y: 2.25, w: 2.7, h: 0.3,
      fontSize: 9, fontFace: "Calibri", color: COLORS.muted,
      align: "center", margin: 0
    });
    slide3.addText(uni.count + " initiatives tracked", {
      x: x + 0.1, y: 2.6, w: 2.7, h: 0.3,
      fontSize: 12, fontFace: "Calibri", color: COLORS.secondary,
      bold: true, align: "center", margin: 0
    });
    slide3.addText(uni.highlight, {
      x: x + 0.1, y: 2.95, w: 2.7, h: 0.4,
      fontSize: 10, fontFace: "Calibri", color: COLORS.dark,
      align: "center", italic: true, margin: 0
    });
  });

  // Key domains section
  slide3.addText("Key Technology Domains", {
    x: 0.5, y: 3.7, w: 9, h: 0.4,
    fontSize: 16, fontFace: "Georgia", color: COLORS.primary,
    bold: true, margin: 0
  });

  const domains = [
    "Autonomous Systems", "Space & Aerospace", "AI/ML & Genertic AI",
    "Robotics", "Biotech & Synbio", "Clean Energy"
  ];

  domains.forEach((domain, i) => {
    const x = 0.5 + (i % 3) * 3.1;
    const y = 4.2 + Math.floor(i / 3) * 0.55;
    slide3.addImage({ data: icons.check, x: x, y: y + 0.05, w: 0.25, h: 0.25 });
    slide3.addText(domain, {
      x: x + 0.35, y: y, w: 2.7, h: 0.35,
      fontSize: 13, fontFace: "Calibri", color: COLORS.dark, margin: 0
    });
  });

  // ============================================
  // SLIDE 4: Top Initiatives
  // ============================================
  let slide4 = pres.addSlide();
  slide4.background = { color: COLORS.light };

  slide4.addText("Tier 1: Immediate Spinout Potential", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 32, fontFace: "Georgia", color: COLORS.primary,
    bold: true, margin: 0
  });

  // Initiative cards (2x2 grid)
  const topInitiatives = [
    { name: "TUM Autonomous Motorsport", tech: "Autonomous Driving", achievement: "Won Indy Autonomous Challenge 4x" },
    { name: "TUM Boring", tech: "Tunneling & Infrastructure", achievement: "3x consecutive competition winner" },
    { name: "CompVis (LMU)", tech: "Generative AI", achievement: "Created Stable Diffusion" },
    { name: "WARR", tech: "Space & Rocketry", achievement: "First EU student cryo rocket" }
  ];

  topInitiatives.forEach((init, i) => {
    const x = 0.5 + (i % 2) * 4.7;
    const y = 1.0 + Math.floor(i / 2) * 1.55;

    slide4.addShape(pres.shapes.RECTANGLE, {
      x: x, y: y, w: 4.5, h: 1.4,
      fill: { color: COLORS.white },
      shadow: { type: "outer", blur: 3, offset: 2, angle: 45, opacity: 0.15 }
    });
    slide4.addShape(pres.shapes.RECTANGLE, {
      x: x, y: y, w: 0.08, h: 1.4,
      fill: { color: COLORS.secondary }
    });

    slide4.addImage({ data: icons.trophy, x: x + 0.2, y: y + 0.15, w: 0.4, h: 0.4 });
    slide4.addText(init.name, {
      x: x + 0.7, y: y + 0.15, w: 3.6, h: 0.35,
      fontSize: 14, fontFace: "Calibri", color: COLORS.primary,
      bold: true, margin: 0
    });
    slide4.addText(init.tech, {
      x: x + 0.7, y: y + 0.5, w: 3.6, h: 0.3,
      fontSize: 11, fontFace: "Calibri", color: COLORS.secondary, margin: 0
    });
    slide4.addText(init.achievement, {
      x: x + 0.2, y: y + 0.9, w: 4.1, h: 0.35,
      fontSize: 11, fontFace: "Calibri", color: COLORS.dark,
      italic: true, margin: 0
    });
  });

  // More initiatives teaser
  slide4.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 4.2, w: 9, h: 1.2,
    fill: { color: COLORS.primary }
  });
  slide4.addText("+ 10 More Tier 1-2 Initiatives Ready for Support", {
    x: 0.5, y: 4.35, w: 9, h: 0.5,
    fontSize: 18, fontFace: "Georgia", color: COLORS.white,
    bold: true, align: "center", margin: 0
  });
  slide4.addText("Falcon Vision (Rescue Drones) • RoboTUM (Humanoids) • Hydro2Motion (H2) • TUM.ai (AI Pipeline) • START Munich (Unicorn Alumni)", {
    x: 0.5, y: 4.85, w: 9, h: 0.4,
    fontSize: 11, fontFace: "Calibri", color: COLORS.muted,
    align: "center", margin: 0
  });

  // ============================================
  // SLIDE 5: Support Model
  // ============================================
  let slide5 = pres.addSlide();
  slide5.background = { color: COLORS.light };

  slide5.addText("Our Support Model", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 32, fontFace: "Georgia", color: COLORS.primary,
    bold: true, margin: 0
  });

  slide5.addText("What high-potential initiatives need at spinout", {
    x: 0.5, y: 0.8, w: 9, h: 0.4,
    fontSize: 14, fontFace: "Calibri", color: COLORS.muted,
    italic: true, margin: 0
  });

  // Four pillars
  const pillars = [
    { icon: icons.laptop, title: "Compute", desc: "GPU/Cloud credits for AI training & simulations", partner: "Compute Partners" },
    { icon: icons.industry, title: "Space", desc: "Co-working, labs, and prototyping facilities", partner: "We Provide" },
    { icon: icons.handshake, title: "Customers", desc: "C-level intros to enterprise partners", partner: "Our Network" },
    { icon: icons.chart, title: "Capital", desc: "Angel tickets from founder network", partner: "Angel Investors" }
  ];

  pillars.forEach((pillar, i) => {
    const x = 0.5 + (i * 2.35);

    // Card
    slide5.addShape(pres.shapes.RECTANGLE, {
      x: x, y: 1.4, w: 2.2, h: 2.8,
      fill: { color: COLORS.white },
      shadow: { type: "outer", blur: 3, offset: 2, angle: 45, opacity: 0.15 }
    });

    // Icon circle
    slide5.addShape(pres.shapes.OVAL, {
      x: x + 0.7, y: 1.6, w: 0.8, h: 0.8,
      fill: { color: COLORS.light }
    });
    slide5.addImage({ data: pillar.icon, x: x + 0.85, y: 1.75, w: 0.5, h: 0.5 });

    slide5.addText(pillar.title, {
      x: x + 0.1, y: 2.55, w: 2, h: 0.4,
      fontSize: 16, fontFace: "Georgia", color: COLORS.primary,
      bold: true, align: "center", margin: 0
    });
    slide5.addText(pillar.desc, {
      x: x + 0.1, y: 3.0, w: 2, h: 0.8,
      fontSize: 11, fontFace: "Calibri", color: COLORS.dark,
      align: "center", margin: 0
    });
    slide5.addText(pillar.partner, {
      x: x + 0.1, y: 3.8, w: 2, h: 0.3,
      fontSize: 10, fontFace: "Calibri", color: COLORS.secondary,
      bold: true, align: "center", margin: 0
    });
  });

  // Value prop
  slide5.addText("Initiatives bring: Proven technology + Cohesive teams + Years of R&D", {
    x: 0.5, y: 4.5, w: 9, h: 0.4,
    fontSize: 14, fontFace: "Calibri", color: COLORS.primary,
    align: "center", bold: true, margin: 0
  });
  slide5.addText("We add: Resources + Network + Capital to accelerate spinout", {
    x: 0.5, y: 4.9, w: 9, h: 0.4,
    fontSize: 14, fontFace: "Calibri", color: COLORS.secondary,
    align: "center", margin: 0
  });

  // ============================================
  // SLIDE 6: The Ask - Compute Partners
  // ============================================
  let slide6 = pres.addSlide();
  slide6.background = { color: COLORS.primary };

  slide6.addText("For Compute Partners", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 32, fontFace: "Georgia", color: COLORS.white,
    bold: true, margin: 0
  });

  slide6.addText("AWS, Google Cloud, NVIDIA, CoreWeave, Lambda Labs", {
    x: 0.5, y: 0.85, w: 9, h: 0.35,
    fontSize: 13, fontFace: "Calibri", color: COLORS.muted, margin: 0
  });

  // What we offer / What we ask
  slide6.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 1.4, w: 4.4, h: 2.6,
    fill: { color: COLORS.white }
  });
  slide6.addText("What You Get", {
    x: 0.7, y: 1.55, w: 4, h: 0.4,
    fontSize: 16, fontFace: "Georgia", color: COLORS.primary,
    bold: true, margin: 0
  });
  slide6.addText([
    { text: "Early access to cutting-edge AI projects", options: { bullet: true, breakLine: true } },
    { text: "Brand visibility with top technical talent", options: { bullet: true, breakLine: true } },
    { text: "Pipeline of potential enterprise customers", options: { bullet: true, breakLine: true } },
    { text: "Co-marketing opportunities", options: { bullet: true, breakLine: true } },
    { text: "Direct feedback on platform capabilities", options: { bullet: true } }
  ], {
    x: 0.7, y: 2.0, w: 4, h: 1.8,
    fontSize: 12, fontFace: "Calibri", color: COLORS.dark, margin: 0
  });

  slide6.addShape(pres.shapes.RECTANGLE, {
    x: 5.1, y: 1.4, w: 4.4, h: 2.6,
    fill: { color: COLORS.secondary }
  });
  slide6.addText("What We Ask", {
    x: 5.3, y: 1.55, w: 4, h: 0.4,
    fontSize: 16, fontFace: "Georgia", color: COLORS.white,
    bold: true, margin: 0
  });
  slide6.addText([
    { text: "Compute credits pool for initiatives", options: { bullet: true, breakLine: true } },
    { text: "Technical mentorship (optional)", options: { bullet: true, breakLine: true } },
    { text: "Priority access to new capabilities", options: { bullet: true, breakLine: true } },
    { text: "Joint case studies on successes", options: { bullet: true } }
  ], {
    x: 5.3, y: 2.0, w: 4, h: 1.8,
    fontSize: 12, fontFace: "Calibri", color: COLORS.white, margin: 0
  });

  // CTA
  slide6.addShape(pres.shapes.RECTANGLE, {
    x: 2.5, y: 4.3, w: 5, h: 1.0,
    fill: { color: COLORS.accent }
  });
  slide6.addText("Pilot: 5-10 initiatives, $50-100K credits/year", {
    x: 2.5, y: 4.45, w: 5, h: 0.4,
    fontSize: 14, fontFace: "Calibri", color: COLORS.dark,
    bold: true, align: "center", margin: 0
  });
  slide6.addText("Measurable impact, quarterly reports, success stories", {
    x: 2.5, y: 4.85, w: 5, h: 0.35,
    fontSize: 11, fontFace: "Calibri", color: COLORS.dark,
    align: "center", margin: 0
  });

  // ============================================
  // SLIDE 7: The Ask - Angel Investors
  // ============================================
  let slide7 = pres.addSlide();
  slide7.background = { color: COLORS.primary };

  slide7.addText("For Angel Investors", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 32, fontFace: "Georgia", color: COLORS.white,
    bold: true, margin: 0
  });

  slide7.addText("Founder Network Members, Deep Tech Angels, Family Offices", {
    x: 0.5, y: 0.85, w: 9, h: 0.35,
    fontSize: 13, fontFace: "Calibri", color: COLORS.muted, margin: 0
  });

  // Investment thesis
  slide7.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 1.4, w: 9, h: 1.6,
    fill: { color: COLORS.white }
  });
  slide7.addText("Why This Pipeline Is Different", {
    x: 0.7, y: 1.5, w: 8.6, h: 0.4,
    fontSize: 16, fontFace: "Georgia", color: COLORS.primary,
    bold: true, margin: 0
  });

  const reasons = [
    "Teams with 2-5 years working together",
    "Deep technical expertise already proven",
    "Real prototypes and competition wins"
  ];
  reasons.forEach((reason, i) => {
    slide7.addImage({ data: icons.check, x: 0.7 + (i * 3), y: 2.0, w: 0.3, h: 0.3 });
    slide7.addText(reason, {
      x: 1.1 + (i * 3), y: 2.0, w: 2.7, h: 0.4,
      fontSize: 12, fontFace: "Calibri", color: COLORS.dark, margin: 0
    });
  });
  slide7.addText("De-risked compared to typical pre-seed investments", {
    x: 0.7, y: 2.5, w: 8.6, h: 0.35,
    fontSize: 12, fontFace: "Calibri", color: COLORS.secondary,
    italic: true, margin: 0
  });

  // Track record
  slide7.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 3.2, w: 4.4, h: 1.6,
    fill: { color: COLORS.secondary }
  });
  slide7.addText("Munich Track Record", {
    x: 0.7, y: 3.35, w: 4, h: 0.35,
    fontSize: 14, fontFace: "Georgia", color: COLORS.white,
    bold: true, margin: 0
  });
  slide7.addText([
    { text: "Celonis: €13B valuation", options: { bullet: true, breakLine: true } },
    { text: "Lilium: IPO (eVTOL)", options: { bullet: true, breakLine: true } },
    { text: "Isar Aerospace: €2.5B", options: { bullet: true } }
  ], {
    x: 0.7, y: 3.75, w: 4, h: 1.0,
    fontSize: 12, fontFace: "Calibri", color: COLORS.white, margin: 0
  });

  // The ask
  slide7.addShape(pres.shapes.RECTANGLE, {
    x: 5.1, y: 3.2, w: 4.4, h: 1.6,
    fill: { color: COLORS.accent }
  });
  slide7.addText("The Ask", {
    x: 5.3, y: 3.35, w: 4, h: 0.35,
    fontSize: 14, fontFace: "Georgia", color: COLORS.dark,
    bold: true, margin: 0
  });
  slide7.addText([
    { text: "Join our angel syndicate", options: { bullet: true, breakLine: true } },
    { text: "€25-50K tickets per spinout", options: { bullet: true, breakLine: true } },
    { text: "3-5 investments per year", options: { bullet: true } }
  ], {
    x: 5.3, y: 3.75, w: 4, h: 1.0,
    fontSize: 12, fontFace: "Calibri", color: COLORS.dark, margin: 0
  });

  // ============================================
  // SLIDE 8: Next Steps / CTA
  // ============================================
  let slide8 = pres.addSlide();
  slide8.background = { color: COLORS.dark };

  slide8.addText("Let's Build the Future Together", {
    x: 0.5, y: 1.0, w: 9, h: 0.8,
    fontSize: 36, fontFace: "Georgia", color: COLORS.white,
    bold: true, align: "center", margin: 0
  });

  slide8.addImage({ data: icons.rocketDark, x: 4.5, y: 2.0, w: 1, h: 1 });

  // Next steps
  slide8.addText("Next Steps", {
    x: 0.5, y: 3.2, w: 9, h: 0.4,
    fontSize: 18, fontFace: "Georgia", color: COLORS.secondary,
    align: "center", margin: 0
  });

  const steps = [
    "1. Schedule deep-dive meeting",
    "2. Review initiative database",
    "3. Identify pilot candidates",
    "4. Launch partnership Q2 2026"
  ];

  steps.forEach((step, i) => {
    slide8.addText(step, {
      x: 0.5, y: 3.7 + (i * 0.35), w: 9, h: 0.35,
      fontSize: 14, fontFace: "Calibri", color: COLORS.white,
      align: "center", margin: 0
    });
  });

  // Contact
  slide8.addShape(pres.shapes.RECTANGLE, {
    x: 3, y: 5.0, w: 4, h: 0.5,
    fill: { color: COLORS.secondary }
  });
  slide8.addText("Contact: bastianburger89@gmail.com", {
    x: 3, y: 5.07, w: 4, h: 0.35,
    fontSize: 13, fontFace: "Calibri", color: COLORS.white,
    bold: true, align: "center", margin: 0
  });

  // Save presentation
  const outputPath = "/sessions/sharp-adoring-fermat/mnt/UnicornInitiative/Student_Initiatives_Partnership_Deck.pptx";
  await pres.writeFile({ fileName: outputPath });
  console.log("Presentation saved to:", outputPath);
}

createPresentation().catch(console.error);
