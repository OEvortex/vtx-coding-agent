import { useEffect, useRef, useState, useMemo } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Search, RotateCcw, ZoomIn, ZoomOut, Maximize2, Minimize2, Eye, EyeOff, Cpu, Activity, Terminal, Layers, ShieldAlert, Sliders } from "lucide-react";
import graphData from "../content/code-graph.json";

interface Node {
  id: string;
  name: string;
  path: string;
  size: number;
  group: string;
  community: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  fx?: number;
  fy?: number;
}

interface Link {
  source: string;
  target: string;
  value: number;
  relations: string[];
  sourceNode?: Node;
  targetNode?: Node;
}

const GROUP_COLORS: Record<string, string> = {
  core: "#A3E635",      // acid lime
  agents: "#FBBF24",     // amber
  tools: "#60A5FA",      // soft blue
  memory: "#34D399",     // emerald
  events: "#F59E0B",     // orange
  extensions: "#A78BFA", // violet
  interface: "#F472B6",  // pink
  watchers: "#22D3EE",   // cyan
  kg: "#E879F9",         // fuchsia
  root: "#71717A",       // zinc
};

export default function CodebaseGraph() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [nodes, setNodes] = useState<Node[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [hoveredNode, setHoveredNode] = useState<Node | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Node[]>([]);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [showLabels, setShowLabels] = useState(true);
  const [isMaximized, setIsMaximized] = useState(false);

  // Zoom/pan state
  const [transform, setTransform] = useState({ x: 0, y: 0, k: 0.36 });
  const isDraggingBackground = useRef(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const transformRef = useRef(transform);
  transformRef.current = transform;

  // Active dragging of nodes
  const draggedNode = useRef<Node | null>(null);

  // Generate random stars for background (Cosmic map theme)
  const backgroundStars = useMemo(() => {
    const temp: { x: number; y: number; size: number; brightness: number; speed: number }[] = [];
    const limit = 2000;
    for (let i = 0; i < 250; i++) {
      temp.push({
        x: (Math.random() - 0.5) * limit,
        y: (Math.random() - 0.5) * limit,
        size: Math.random() * 1.5 + 0.4,
        brightness: 0.2 + Math.random() * 0.8,
        speed: 0.05 + Math.random() * 0.25,
      });
    }
    return temp;
  }, []);

  // Initialize nodes and links
  useEffect(() => {
    const rawNodes: Node[] = graphData.nodes.map((n) => ({
      ...n,
      x: n.group === "root" ? 0 : (Math.random() - 0.5) * 2000,
      y: n.group === "root" ? 0 : (Math.random() - 0.5) * 2000,
      vx: 0,
      vy: 0,
    }));

    const nodeMap = new Map<string, Node>();
    rawNodes.forEach((n) => nodeMap.set(n.id, n));

    const rawLinks: Link[] = graphData.links
      .map((l) => ({
        ...l,
        sourceNode: nodeMap.get(l.source),
        targetNode: nodeMap.get(l.target),
      }))
      .filter((l) => l.sourceNode && l.targetNode);

    setNodes(rawNodes);
    setLinks(rawLinks);

    // Center the graph initial pan based on container size
    if (containerRef.current) {
      const { width, height } = containerRef.current.getBoundingClientRect();
      setTransform({ x: width / 2, y: height / 2, k: 0.36 });
    }
  }, []);

  // Force recalculate canvas size when maximized toggles
  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    // Small delay to let DOM render and stabilize widths
    const timer = setTimeout(() => {
      const { width, height } = container.getBoundingClientRect();
      canvas.width = width * window.devicePixelRatio;
      canvas.height = height * window.devicePixelRatio;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;

      const ctx = canvas.getContext("2d");
      if (ctx) {
        ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
      }
      setTransform({ x: width / 2, y: height / 2, k: 0.36 });
    }, 100);

    return () => clearTimeout(timer);
  }, [isMaximized]);

  // Search logic
  useEffect(() => {
    if (!searchQuery.trim()) {
      setSearchResults([]);
      return;
    }
    const q = searchQuery.toLowerCase();
    const matches = nodes.filter(
      (n) => n.name.toLowerCase().includes(q) || n.path.toLowerCase().includes(q)
    );
    setSearchResults(matches.slice(0, 10));
  }, [searchQuery, nodes]);

  // Highlight connections
  const connectedNodeIds = useMemo(() => {
    const active = selectedNode || hoveredNode;
    if (!active) return null;
    const set = new Set<string>([active.id]);
    links.forEach((l) => {
      if (l.source === active.id) set.add(l.target);
      if (l.target === active.id) set.add(l.source);
    });
    return set;
  }, [hoveredNode, selectedNode, links]);

  // Find exact import/export relations for active selected node to allow traversal
  const activeNodeRelations = useMemo(() => {
    if (!selectedNode) return { imports: [], exports: [] };
    const imports: Node[] = [];
    const exports: Node[] = [];

    links.forEach((l) => {
      if (l.source === selectedNode.id && l.targetNode) {
        imports.push(l.targetNode);
      }
      if (l.target === selectedNode.id && l.sourceNode) {
        exports.push(l.sourceNode);
      }
    });

    return {
      imports: imports.slice(0, 4), // slice to fit telemetry card cleanly
      exports: exports.slice(0, 4),
    };
  }, [selectedNode, links]);

  // Main simulation and canvas render loop
  useEffect(() => {
    if (nodes.length === 0) return;

    let animId: number;
    let alpha = 1;
    const decay = 0.992; // Even slower decay for smoother universe drift (was 0.99)
    const chargeStrength = -320; // Significantly stronger repulsion for wider spacing (was -160)
    const linkStrength = 0.045; // Softened link spring constant to allow stretching (was 0.055)
    const gravity = 0.0035; // Minimal gravity so nodes spread widely across canvas (was 0.007)
    const desiredDist = 220; // Massive link distance for spacious constellation (was 135)

    const tick = () => {
      if (alpha < 0.005) {
        alpha = 0.005;
      } else {
        alpha *= decay;
      }

      // 1. Repulsion and Collision resolution (Combined for maximum spacing & performance)
      for (let i = 0; i < nodes.length; i++) {
        const n1 = nodes[i];
        const r1 = n1.group === "root" ? 24 : Math.max(3.5, Math.min(10, 3 + n1.size * 0.15));
        for (let j = i + 1; j < nodes.length; j++) {
          const n2 = nodes[j];
          const r2 = n2.group === "root" ? 24 : Math.max(3.5, Math.min(10, 3 + n2.size * 0.15));
          const dx = n1.x - n2.x;
          const dy = n1.y - n2.y;
          const distSq = dx * dx + dy * dy + 1e-4;

          if (distSq < 490000) { // Limit repulsion interaction to 700px radius
            const dist = Math.sqrt(distSq);
            
            // Standard inverse-distance charge force
            let force = (chargeStrength * alpha) / (distSq + 250);
            
            // Collision resolution push if nodes are overlapping or very close
            // Enforce a massive gap of 250px if one of the nodes is the central sun
            const isRootNode = n1.group === "root" || n2.group === "root";
            const minDist = isRootNode ? r1 + r2 + 250 : r1 + r2 + 75;
            
            if (dist < minDist) {
              const overlap = minDist - dist;
              // Add strong linear push to ensure separation
              force -= (overlap / minDist) * 2.8 * alpha;
            }

            const fx = force * (dx / dist);
            const fy = force * (dy / dist);

            n1.vx -= fx;
            n1.vy -= fy;
            n2.vx += fx;
            n2.vy += fy;
          }
        }
      }

      // 2. Attraction (Link spring force)
      links.forEach((l) => {
        const s = l.sourceNode!;
        const t = l.targetNode!;
        const dx = t.x - s.x;
        const dy = t.y - s.y;
        const dist = Math.sqrt(dx * dx + dy * dy) + 1e-4;
        
        // Connect central sun links with a larger planetary orbit range of 350px
        const isRootLink = s.group === "root" || t.group === "root";
        const currentDesiredDist = isRootLink ? 350 : desiredDist;
        
        const force = (dist - currentDesiredDist) * linkStrength * alpha;
        const fx = force * (dx / dist);
        const fy = force * (dy / dist);
        s.vx += fx;
        s.vy += fy;
        t.vx -= fx;
        t.vy -= fy;
      });

      // 3. Gravity and position updates
      nodes.forEach((n) => {
        if (n.group === "root") {
          // Centrally lock the sun node to origin
          n.x = 0;
          n.y = 0;
          n.vx = 0;
          n.vy = 0;
          return;
        }

        if (n.fx !== undefined && n.fy !== undefined) {
          n.x = n.fx;
          n.y = n.fy;
          n.vx = 0;
          n.vy = 0;
        } else {
          // Central gravity pull (constellation core)
          n.vx -= n.x * gravity * alpha;
          n.vy -= n.y * gravity * alpha;

          // Drag coefficient
          n.vx *= 0.86;
          n.vy *= 0.86;

          n.x += n.vx;
          n.y += n.vy;
        }
      });

      // 4. Render
      draw();
      animId = requestAnimationFrame(tick);
    };

    const draw = () => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      // Clear transparently to inherit parent container background (#0A0A0B)
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.save();

      // Zoom & pan
      const { x, y, k } = transformRef.current;
      ctx.translate(x, y);
      ctx.scale(k, k);

      const time = Date.now();
      const active = selectedNode || hoveredNode;

      // ── 1. COSMIC NEBULAE GLOWS (Site matched colors, faint radial gradients) ──
      const nebulae = [
        { x: -500, y: -400, r: 700, color: "rgba(59, 130, 246, 0.015)" },   // Blue
        { x: 600, y: 500, r: 800, color: "rgba(139, 92, 246, 0.012)" },    // Purple
        { x: -300, y: 600, r: 600, color: "rgba(212, 175, 55, 0.015)" },    // Gold/Yellow
        { x: 400, y: -600, r: 650, color: "rgba(16, 185, 129, 0.012)" }     // Green
      ];
      nebulae.forEach((neb) => {
        const grad = ctx.createRadialGradient(neb.x, neb.y, 0, neb.x, neb.y, neb.r);
        grad.addColorStop(0, neb.color);
        grad.addColorStop(0.5, neb.color.replace("0.01", "0.004"));
        grad.addColorStop(1, "rgba(10, 10, 11, 0)");
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(neb.x, neb.y, neb.r, 0, 2 * Math.PI);
        ctx.fill();
      });

      // ── 2. TWINKLING BACKGROUND STARS (Using site text color #FAFAF9) ──
      backgroundStars.forEach((star) => {
        const twinkle = Math.sin((time * 0.0018 * star.speed) + star.brightness * 10) * 0.35 + 0.65;
        ctx.fillStyle = "#FAFAF9";
        ctx.globalAlpha = star.brightness * 0.14 * twinkle;
        ctx.beginPath();
        ctx.arc(star.x, star.y, star.size, 0, 2 * Math.PI);
        ctx.fill();
      });
      ctx.globalAlpha = 1;

      // ── 3. CENTRAL GALAXY SPIRAL ARMS (Faint orbits of star dust) ──
      ctx.strokeStyle = "rgba(250, 250, 249, 0.05)";
      ctx.lineWidth = 1;
      const drawGalaxySpiral = (arms: number, loops: number, maxRadius: number, direction: number) => {
        ctx.save();
        for (let i = 0; i < arms; i++) {
          const startAngle = (i * Math.PI * 2) / arms + (time * 0.000012 * direction);
          ctx.beginPath();
          let first = true;
          for (let r = 10; r < maxRadius; r += 15) {
            const angle = startAngle + (r / maxRadius) * loops * Math.PI * 2 * direction;
            const sx = Math.cos(angle) * r;
            const sy = Math.sin(angle) * r;
            if (first) {
              ctx.moveTo(sx, sy);
              first = false;
            } else {
              ctx.lineTo(sx, sy);
            }
          }
          ctx.stroke();
        }
        ctx.restore();
      };
      drawGalaxySpiral(4, 1.3, 1000, 1);
      drawGalaxySpiral(3, 1.1, 750, -0.8);

      // ── 4. DRAW HOLOGRAPHIC BACKGROUND GRID (STAR CHART STYLE) ──
      ctx.strokeStyle = "rgba(26, 26, 26, 0.03)";
      ctx.lineWidth = 0.8 / k;
      const gridSpacing = 150;
      const gridLimit = 2000;

      ctx.beginPath();
      for (let xG = -gridLimit; xG <= gridLimit; xG += gridSpacing) {
        ctx.moveTo(xG, -gridLimit);
        ctx.lineTo(xG, gridLimit);
      }
      for (let yG = -gridLimit; yG <= gridLimit; yG += gridSpacing) {
        ctx.moveTo(-gridLimit, yG);
        ctx.lineTo(gridLimit, yG);
      }
      ctx.stroke();

      // Constellation quadrant coordinates markings
      ctx.fillStyle = "rgba(26, 26, 26, 0.2)";
      ctx.font = "8px monospace";
      for (let xG = -1200; xG <= 1200; xG += 300) {
        if (xG !== 0) {
          ctx.fillText(`RA ${Math.abs(xG / 100).toFixed(1)}h`, xG + 5, 12);
        }
      }
      for (let yG = -1200; yG <= 1200; yG += 300) {
        if (yG !== 0) {
          ctx.fillText(`DEC ${yG > 0 ? "+" : ""}${(yG / 10).toFixed(0)}°`, 5, yG - 5);
        }
      }

      // Concentric dotted radar orbital paths
      ctx.strokeStyle = "rgba(26, 26, 26, 0.025)";
      ctx.setLineDash([2, 6]);
      for (let r = 200; r <= 1200; r += 200) {
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, 2 * Math.PI);
        ctx.stroke();
      }
      ctx.setLineDash([]);

      // ── DRAW CONSTELLATION EDGES (LINKS) AND SHOOTING-STAR DATA PACKETS ──
      links.forEach((l, index) => {
        const s = l.sourceNode!;
        const t = l.targetNode!;

        let opacity = 0.085;
        let strokeStyle = "rgba(26, 26, 26, 0.14)";
        let isTargeted = false;

        if (connectedNodeIds) {
          const isSrcConnected = connectedNodeIds.has(s.id);
          const isTgtConnected = connectedNodeIds.has(t.id);
          
          if (isSrcConnected && isTgtConnected && (s.id === active?.id || t.id === active?.id)) {
            opacity = 0.7;
            strokeStyle = GROUP_COLORS[active.group] || "#A3E635";
            isTargeted = true;
          } else {
            opacity = 0.06;
          }
        }

        // Draw constellation link
        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(t.x, t.y);
        ctx.strokeStyle = strokeStyle;
        ctx.lineWidth = isTargeted ? 1.8 / k : 0.7 / k;
        ctx.globalAlpha = opacity;
        ctx.stroke();

        // Flowing stellar energy packets (shooting stars with trails)
        if (isTargeted) {
          const progress = ((time / 1000) + index * 0.15) % 1.0;
          const steps = 4;
          
          for (let step = 0; step < steps; step++) {
            const trailProgress = progress - (step * 0.02);
            if (trailProgress >= 0 && trailProgress <= 1) {
              const tx = s.x + (t.x - s.x) * trailProgress;
              const ty = s.y + (t.y - s.y) * trailProgress;
              const size = (2.6 * (1 - step / steps)) / k;
              
              ctx.beginPath();
              ctx.arc(tx, ty, size, 0, 2 * Math.PI);
              ctx.fillStyle = strokeStyle;
              ctx.globalAlpha = opacity * 1.8 * (1 - step / steps);
              ctx.fill();
            }
          }
        }
      });

      ctx.globalAlpha = 1;

      // ── DRAW STELLAR STAR NODES AND TARGET INDICATORS ──
      nodes.forEach((n) => {
        const color = GROUP_COLORS[n.group] || "#71717A";
        
        // Handle central sun root node styling
        if (n.group === "root") {
          const sunRadius = 26;
          ctx.save();
          
          // 1. Rotating solar gold flares (site matched color D4AF37)
          const numRays = 12;
          ctx.strokeStyle = "rgba(212, 175, 55, 0.35)";
          ctx.lineWidth = 1.5 / k;
          const rotSpeed = time * 0.0003;
          
          for (let r = 0; r < numRays; r++) {
            const angle = (r * Math.PI * 2) / numRays + rotSpeed;
            const flareLen = sunRadius + 10 + Math.sin(time * 0.0025 + r) * 5;
            ctx.beginPath();
            ctx.moveTo(n.x + Math.cos(angle) * sunRadius, n.y + Math.sin(angle) * sunRadius);
            ctx.lineTo(n.x + Math.cos(angle) * flareLen, n.y + Math.sin(angle) * flareLen);
            ctx.stroke();
          }

          // 2. Large burning atmospheric corona
          const coronaGrad = ctx.createRadialGradient(n.x, n.y, sunRadius * 0.8, n.x, n.y, sunRadius * 2.5);
          coronaGrad.addColorStop(0, "rgba(212, 175, 55, 0.45)"); // Gold
          coronaGrad.addColorStop(0.3, "rgba(245, 158, 11, 0.2)"); // Orange
          coronaGrad.addColorStop(1, "rgba(10, 10, 11, 0)");
          ctx.fillStyle = coronaGrad;
          ctx.beginPath();
          ctx.arc(n.x, n.y, sunRadius * 2.5, 0, 2 * Math.PI);
          ctx.fill();

          // 3. Glowing core (shadow blur)
          ctx.shadowBlur = 30;
          ctx.shadowColor = "#D4AF37";
          ctx.fillStyle = "#FAFAF9"; // Light text on dark canvas
          ctx.beginPath();
          ctx.arc(n.x, n.y, sunRadius, 0, 2 * Math.PI);
          ctx.fill();

          ctx.shadowBlur = 0; // remove shadow

          // Gold border stroke
          ctx.strokeStyle = "#D4AF37";
          ctx.lineWidth = 2.5;
          ctx.stroke();

          // 4. Star chart metadata labels for the sun
          if (showLabels) {
            ctx.font = "bold 11px monospace";
            ctx.fillStyle = "#FAFAF9";
            ctx.textAlign = "center";
            ctx.fillText("☀️ " + n.name.toUpperCase(), n.x, n.y - sunRadius - 15);
            ctx.font = "7px monospace";
            ctx.fillStyle = "rgba(250, 250, 249, 0.55)";
            ctx.fillText("GALACTIC SYSTEM CORE", n.x, n.y + sunRadius + 14);
          }

          ctx.restore();
          return; // Skip normal node drawing routines for this central sun node
        }

        const radius = Math.max(3.5, Math.min(10, 3 + n.size * 0.15));

        let opacity = 1;
        let isHighlighted = false;

        if (connectedNodeIds) {
          if (connectedNodeIds.has(n.id)) {
            opacity = 1;
            isHighlighted = n.id === active?.id;
          } else {
            opacity = 0.16;
          }
        }

        ctx.save();
        ctx.globalAlpha = opacity;

        // Faint atmospheric corona pulsing around the star
        const coronaPulse = 1.35 + Math.sin(time * 0.0018 + n.community) * 0.12;
        ctx.beginPath();
        ctx.arc(n.x, n.y, radius * coronaPulse, 0, 2 * Math.PI);
        ctx.fillStyle = color;
        ctx.globalAlpha = opacity * (isHighlighted ? 0.25 : 0.08);
        ctx.fill();

        // Glowing center core (stellar mass shadow)
        ctx.shadowBlur = isHighlighted ? 18 : 6;
        ctx.shadowColor = color;
        
        ctx.beginPath();
        ctx.arc(n.x, n.y, radius, 0, 2 * Math.PI);
        ctx.fillStyle = color;
        ctx.globalAlpha = opacity;
        ctx.fill();
        
        ctx.shadowBlur = 0; // remove shadow

        // Star chart HUD indicators for selected / hovered node
        if (isHighlighted) {
          const rotSpeed = time / 900;
          
          // Outer clockwise notched circle
          ctx.strokeStyle = color;
          ctx.lineWidth = 1 / k;
          ctx.beginPath();
          ctx.arc(n.x, n.y, radius + 7, rotSpeed, rotSpeed + Math.PI * 0.4);
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(n.x, n.y, radius + 7, rotSpeed + Math.PI, rotSpeed + Math.PI * 1.4);
          ctx.stroke();

          // Inner counter-clockwise notched circle
          ctx.strokeStyle = "rgba(26, 26, 26, 0.35)";
          ctx.beginPath();
          ctx.arc(n.x, n.y, radius + 4, -rotSpeed * 1.5, -rotSpeed * 1.5 + Math.PI * 0.3);
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(n.x, n.y, radius + 4, -rotSpeed * 1.5 + Math.PI, -rotSpeed * 1.5 + Math.PI * 1.3);
          ctx.stroke();

          // Coordinate grid crosshairs
          ctx.strokeStyle = "rgba(26, 26, 26, 0.2)";
          ctx.lineWidth = 0.6 / k;
          ctx.beginPath();
          // Horizontal axes
          ctx.moveTo(n.x - radius - 15, n.y);
          ctx.lineTo(n.x - radius - 6, n.y);
          ctx.moveTo(n.x + radius + 6, n.y);
          ctx.lineTo(n.x + radius + 15, n.y);
          // Vertical axes
          ctx.moveTo(n.x, n.y - radius - 15);
          ctx.lineTo(n.x, n.y - radius - 6);
          ctx.moveTo(n.x, n.y + radius + 6);
          ctx.lineTo(n.x, n.y + radius + 15);
          ctx.stroke();
        }

        // Label drawing (Star chart metadata style)
        if (showLabels && (k > 1.2 || isHighlighted || (connectedNodeIds && connectedNodeIds.has(n.id)))) {
          ctx.font = `${isHighlighted ? "bold" : "normal"} 10px monospace`;
          ctx.fillStyle = isHighlighted ? "#FAFAF9" : "rgba(250, 250, 249, 0.75)";
          ctx.textAlign = "left";

          const labelX = n.x + radius + 7;
          const labelY = n.y + 3;
          ctx.fillText(n.name, labelX, labelY);

          if (isHighlighted) {
            ctx.font = "7px monospace";
            ctx.fillStyle = "rgba(250, 250, 249, 0.45)";
            ctx.fillText(`RA: ${Math.abs(n.x / 100).toFixed(2)}h  DEC: ${(n.y / 10).toFixed(1)}°`, labelX, labelY + 10);
          }
        }

        ctx.restore();
      });

      ctx.restore();
    };

    tick();
    return () => {
      cancelAnimationFrame(animId);
    };
  }, [nodes, links, connectedNodeIds, selectedNode, hoveredNode, showLabels, backgroundStars]);

  // Handle canvas window resizing
  useEffect(() => {
    const handleResize = () => {
      if (isMaximized) return; // handled by the other useEffect
      const canvas = canvasRef.current;
      const container = containerRef.current;
      if (!canvas || !container) return;

      const { width, height } = container.getBoundingClientRect();
      canvas.width = width * window.devicePixelRatio;
      canvas.height = height * window.devicePixelRatio;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;

      const ctx = canvas.getContext("2d");
      if (ctx) ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    };

    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [isMaximized]);

  // Mouse pan/zoom calculations
  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const { x, y, k } = transform;
    const gx = (mx - x) / k;
    const gy = (my - y) / k;

    let hitNode: Node | null = null;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const radius = Math.max(3.5, Math.min(10, 3 + n.size * 0.15));
      const dx = n.x - gx;
      const dy = n.y - gy;
      if (dx * dx + dy * dy < (radius + 5) * (radius + 5)) {
        hitNode = n;
        break;
      }
    }

    if (hitNode) {
      draggedNode.current = hitNode;
      hitNode.fx = hitNode.x;
      hitNode.fy = hitNode.y;
      setSelectedNode(hitNode);
    } else {
      isDraggingBackground.current = true;
      dragStart.current = { x: e.clientX - transform.x, y: e.clientY - transform.y };
      setSelectedNode(null);
    }
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const { x, y, k } = transform;
    const gx = (mx - x) / k;
    const gy = (my - y) / k;

    if (draggedNode.current) {
      draggedNode.current.fx = gx;
      draggedNode.current.fy = gy;
      draggedNode.current.x = gx;
      draggedNode.current.y = gy;
    } else if (isDraggingBackground.current) {
      setTransform({
        x: e.clientX - dragStart.current.x,
        y: e.clientY - dragStart.current.y,
        k,
      });
    } else {
      let hitNode: Node | null = null;
      for (let i = nodes.length - 1; i >= 0; i--) {
        const n = nodes[i];
        const radius = Math.max(3.5, Math.min(10, 3 + n.size * 0.15));
        const dx = n.x - gx;
        const dy = n.y - gy;
        if (dx * dx + dy * dy < (radius + 5) * (radius + 5)) {
          hitNode = n;
          break;
        }
      }
      setHoveredNode(hitNode);
    }
  };

  const handleMouseUp = () => {
    if (draggedNode.current) {
      draggedNode.current.fx = undefined;
      draggedNode.current.fy = undefined;
      draggedNode.current = null;
    }
    isDraggingBackground.current = false;
  };

  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const { x, y, k } = transform;
    const zoomFactor = e.deltaY < 0 ? 1.08 : 0.92;
    const nextK = Math.max(0.1, Math.min(8, k * zoomFactor));

    const gx = (mx - x) / k;
    const gy = (my - y) / k;
    const nextX = mx - gx * nextK;
    const nextY = my - gy * nextK;

    setTransform({ x: nextX, y: nextY, k: nextK });
  };

  const handleZoom = (factor: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { x, y, k } = transform;
    const nextK = Math.max(0.1, Math.min(8, k * factor));
    const cx = canvas.width / (2 * window.devicePixelRatio);
    const cy = canvas.height / (2 * window.devicePixelRatio);

    const gx = (cx - x) / k;
    const gy = (cy - y) / k;
    const nextX = cx - gx * nextK;
    const nextY = cy - gy * nextK;

    setTransform({ x: nextX, y: nextY, k: nextK });
  };

  const handleReset = () => {
    if (containerRef.current) {
      const { width, height } = containerRef.current.getBoundingClientRect();
      nodes.forEach((n) => {
        n.x = n.group === "root" ? 0 : (Math.random() - 0.5) * 2000;
        n.y = n.group === "root" ? 0 : (Math.random() - 0.5) * 2000;
        n.vx = 0;
        n.vy = 0;
      });
      setTransform({ x: width / 2, y: height / 2, k: 0.36 });
      setSelectedNode(null);
      setHoveredNode(null);
    }
  };

  const handleSelectSearched = (node: Node) => {
    setSelectedNode(node);
    setSearchQuery("");
    setSearchResults([]);

    if (containerRef.current) {
      const { width, height } = containerRef.current.getBoundingClientRect();
      const nextK = 1.8;
      setTransform({
        x: width / 2 - node.x * nextK,
        y: height / 2 - node.y * nextK,
        k: nextK,
      });
    }
  };

  const activeNode = selectedNode || hoveredNode;

  return (
    <div className={`w-full h-full flex flex-col relative bg-[#0A0A0B] text-ink select-none font-mono overflow-hidden transition-all ${
      isMaximized ? "fixed inset-0 z-[100] w-screen h-screen" : ""
    }`}>
      {/* Background sweep line scanner */}
      <div className="hud-scanline" />

      {/* Top Telemetry Header Bar */}
      <div className="absolute top-0 inset-x-0 h-10 border-b border-hairline bg-[#111114]/80 backdrop-blur-sm z-30 flex items-center justify-between px-6 text-[9px] uppercase tracking-[0.25em] font-bold">
        <div className="flex items-center gap-2">
          <Activity size={12} className="text-accent" />
          <span className="text-ink-muted">Vtx codebase topology explorer</span>
        </div>
        <div className="flex items-center gap-4 text-ink-faint font-mono text-[8px] tracking-wider uppercase">
          <span>Active files: {nodes.length}</span>
          <span>Links: {links.length}</span>
        </div>
      </div>

      {/* Simulation Workspace */}
      <div ref={containerRef} className="flex-1 w-full h-full relative cursor-grab active:cursor-grabbing min-h-[550px] pt-10">
        <canvas
          ref={canvasRef}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onWheel={handleWheel}
          className="absolute inset-0 block w-full h-full"
        />

        {/* Floating search container */}
        <div className="absolute top-14 left-6 z-25 w-72 flex flex-col gap-1.5" role="search" aria-label="Codebase search">
          <label className="hud-panel flex items-center gap-2 px-3 py-2">
            <Search size={14} className="opacity-50 text-ink-muted" aria-hidden="true" />
            <input
              type="text"
              placeholder="Query target symbol..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="bg-transparent border-none outline-none text-[10px] font-mono w-full text-ink placeholder:text-ink-faint/60 uppercase tracking-widest"
              aria-label="Search codebase symbols"
            />
          </label>

          <AnimatePresence>
            {searchResults.length > 0 && (
              <motion.div
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                className="hud-panel flex flex-col divide-y divide-hairline max-h-56 overflow-y-auto"
              >
                {searchResults.map((n) => (
                  <button
                    key={n.id}
                    onClick={() => handleSelectSearched(n)}
                    className="px-3 py-2 text-left hover:bg-surface cursor-pointer text-[10px] font-mono truncate text-ink flex flex-col gap-0.5"
                  >
                    <span className="font-bold flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-sm" style={{ backgroundColor: GROUP_COLORS[n.group] || "#71717A" }} />
                      {n.name}
                    </span>
                    <span className="text-[8px] text-ink-faint truncate">{n.path}</span>
                  </button>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Bottom Left Panel: Module Legend */}
        <div className="absolute bottom-6 left-6 z-25 flex flex-col gap-3 w-80">
          <div className="hud-panel p-4 hidden md:flex flex-col gap-2 font-mono text-[8.5px] tracking-wider uppercase">
            <span className="font-bold border-b border-hairline pb-1.5 text-ink flex items-center gap-1.5">
              <Layers size={10} /> Code module domains
            </span>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 font-bold">
              {Object.entries(GROUP_COLORS).map(([group, color]) => (
                <div key={group} className="flex items-center gap-2 group cursor-help" title={`Module segment: ${group}`}>
                  <span className="w-2.5 h-2.5 rounded-sm shrink-0 border border-hairline" style={{ backgroundColor: color }} />
                  <span className="text-ink-muted group-hover:text-ink transition-colors">{group}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Floating Controls (Bottom Right) */}
        <div className="absolute bottom-6 right-6 z-25 flex flex-col gap-2" role="toolbar" aria-label="Graph controls">
          <button
            onClick={() => handleZoom(1.3)}
            className="w-8 h-8 flex items-center justify-center border border-hairline bg-surface-2 hover:bg-accent hover:text-[#0A0A0B] hover:border-accent transition-all cursor-pointer text-ink-muted"
            title="Zoom in"
            aria-label="Zoom in"
          >
            <ZoomIn size={14} aria-hidden="true" />
          </button>
          <button
            onClick={() => handleZoom(0.7)}
            className="w-8 h-8 flex items-center justify-center border border-hairline bg-surface-2 hover:bg-accent hover:text-[#0A0A0B] hover:border-accent transition-all cursor-pointer text-ink-muted"
            title="Zoom out"
            aria-label="Zoom out"
          >
            <ZoomOut size={14} aria-hidden="true" />
          </button>
          <button
            onClick={() => setShowLabels(!showLabels)}
            className={`w-8 h-8 flex items-center justify-center border transition-all cursor-pointer ${showLabels ? "bg-accent text-[#0A0A0B] border-accent" : "border-hairline bg-surface-2 text-ink-muted"}`}
            title="Toggle scan labels"
            aria-label={showLabels ? "Hide labels" : "Show labels"}
            aria-pressed={showLabels}
          >
            {showLabels ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}
          </button>
          <button
            onClick={handleReset}
            className="w-8 h-8 flex items-center justify-center border border-hairline bg-surface-2 hover:bg-accent hover:text-[#0A0A0B] hover:border-accent transition-all cursor-pointer text-ink-muted"
            title="Recalibrate map"
            aria-label="Reset graph view"
          >
            <RotateCcw size={14} aria-hidden="true" />
          </button>
          <button
            onClick={() => setIsMaximized(!isMaximized)}
            className="w-8 h-8 flex items-center justify-center border border-hairline bg-surface-2 hover:bg-accent hover:text-[#0A0A0B] hover:border-accent transition-all cursor-pointer text-ink-muted"
            title={isMaximized ? "Exit Fullscreen" : "Maximize Explorer"}
            aria-label={isMaximized ? "Exit fullscreen" : "Maximize graph"}
            aria-pressed={isMaximized}
          >
            {isMaximized ? <Minimize2 size={14} aria-hidden="true" /> : <Maximize2 size={14} aria-hidden="true" />}
          </button>
        </div>

        {/* Selected Target HUD Details & Relations (Top Right) */}
        <AnimatePresence>
          {activeNode && (
            <motion.div
              initial={{ opacity: 0, y: 10, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.97 }}
              className={`absolute top-14 right-6 z-25 w-80 shadow-2xl flex flex-col gap-3.5 text-left border ${
                activeNode.group === "core" || activeNode.group === "tools"
                  ? "bg-[#1A0A0A]/95 border-red-500/30 text-red-400"
                  : "bg-[#111114]/95 border-hairline text-ink"
              } p-4.5 rounded-sm backdrop-blur-md max-h-[82%] overflow-y-auto`}>
              {/* Telemetry Header */}
              <div className="flex flex-col gap-1.5 border-b border-current/15 pb-3">
                <div className="flex items-center justify-between text-[7px] font-mono font-black tracking-[0.22em] uppercase">
                  <span className="flex items-center gap-1.5">
                    <span className={`w-1.5 h-1.5 rounded-full ${selectedNode ? "bg-emerald-500 animate-pulse" : "bg-blue-500 animate-ping"} shrink-0`} />
                    {selectedNode ? "ORBIT LOCKED // SECURED" : "SCANNING CELESTIAL SIGNAL"}
                  </span>
                  <span>{activeNode.group}</span>
                </div>
                
                <div className="flex items-start justify-between gap-3 pt-1">
                  <h4 className="text-sm font-display font-black leading-tight truncate uppercase tracking-wider text-ink">
                    {activeNode.name}
                  </h4>
                  {selectedNode && (
                    <button
                      onClick={() => setSelectedNode(null)}
                      className="text-[7.5px] font-mono border border-current hover:bg-ink hover:text-canvas px-2 py-0.5 uppercase cursor-pointer transition-colors font-black tracking-widest"
                    >
                      release
                    </button>
                  )}
                </div>
              </div>

              {/* Dynamic Radar Sweep Tracker (Constellation local radar map) */}
              <div className="relative w-full h-20 border border-current/10 bg-current/5 mt-0.5 overflow-hidden flex items-center justify-center">
                <svg viewBox="0 0 280 80" className="w-full h-full text-current">
                  {/* Scope lines */}
                  <line x1="0" y1="40" x2="280" y2="40" stroke="currentColor" strokeWidth="0.3" className="opacity-20" />
                  <line x1="140" y1="0" x2="140" y2="80" stroke="currentColor" strokeWidth="0.3" className="opacity-20" />
                  
                  {/* Concentric radar rings */}
                  <circle cx="140" cy="40" r="32" stroke="currentColor" strokeWidth="0.5" fill="none" strokeDasharray="1 3" className="opacity-35" />
                  <circle cx="140" cy="40" r="18" stroke="currentColor" strokeWidth="0.5" fill="none" className="opacity-20" />
                  
                  {/* Ticks */}
                  <line x1="140" y1="5" x2="140" y2="9" stroke="currentColor" strokeWidth="0.8" className="opacity-40" />
                  <line x1="140" y1="71" x2="140" y2="75" stroke="currentColor" strokeWidth="0.8" className="opacity-40" />
                  <line x1="103" y1="40" x2="107" y2="40" stroke="currentColor" strokeWidth="0.8" className="opacity-40" />
                  <line x1="173" y1="40" x2="177" y2="40" stroke="currentColor" strokeWidth="0.8" className="opacity-40" />
                  
                  {/* Rotating sweep line */}
                  <line x1="140" y1="40" x2="140" y2="6" stroke="currentColor" strokeWidth="1" className="origin-[140px_40px] animate-spin opacity-50" />
                  
                  {/* Target center node */}
                  <circle cx="140" cy="40" r="4.5" fill={GROUP_COLORS[activeNode.group] || "#71717A"} className="animate-pulse" />
                  
                  {/* Connected stellar links */}
                  {(() => {
                    const connectedNodes: { id: string; type: "import" | "export" }[] = [];
                    activeNodeRelations.imports.forEach(n => connectedNodes.push({ id: n.id, type: "import" }));
                    activeNodeRelations.exports.forEach(n => connectedNodes.push({ id: n.id, type: "export" }));
                    
                    return connectedNodes.map((n, i) => {
                      const angle = (i * Math.PI * 2) / (connectedNodes.length || 1);
                      const rad = 25;
                      const cx = 140 + rad * Math.cos(angle);
                      const cy = 40 + rad * Math.sin(angle);
                      return (
                        <g key={n.id}>
                          <line x1="140" y1="40" x2={cx} y2={cy} stroke="currentColor" strokeWidth="0.3" strokeDasharray="1 2" className="opacity-20" />
                          <circle cx={cx} cy={cy} r="2.2" fill={n.type === "import" ? "#3b82f6" : "#10b981"} className="opacity-80" />
                        </g>
                      );
                    });
                  })()}
                </svg>
                <div className="absolute bottom-1 right-2 text-[6.5px] uppercase opacity-45 font-mono tracking-widest">
                  COSMIC RESOLUTION
                </div>
              </div>

              {/* Symbol statistics detail */}
              <div className="space-y-3 font-mono text-[9.5px]">
                <div className="flex flex-col gap-1 border border-current/10 bg-current/5 p-2 rounded-sm">
                  <div className="flex justify-between text-[6.5px] opacity-50 font-bold tracking-wider">
                    <span>PATH FILE VECTOR</span>
                    <span>SECURE://</span>
                  </div>
                  <span className="text-[#1A1A1A] truncate font-mono text-[9px] font-bold select-all tracking-tight">{activeNode.path}</span>
                </div>
                
                <div className="grid grid-cols-2 gap-3 pt-1">
                  <div className="flex flex-col gap-1">
                    <span className="opacity-50 font-bold uppercase text-[7px] tracking-wider">STAR NODE MASS</span>
                    <div className="flex items-center gap-2">
                      <span className="text-[#1A1A1A] font-black text-xs">{activeNode.size}</span>
                      <div className="flex-1 h-1.5 bg-current/10 overflow-hidden relative" title={`${activeNode.size} symbols`}>
                        <div className="h-full bg-current opacity-85" style={{ width: `${Math.min(100, (activeNode.size / 50) * 100)}%` }} />
                      </div>
                    </div>
                  </div>
                  
                  <div className="flex flex-col gap-1">
                    <span className="opacity-50 font-bold uppercase text-[7px] tracking-wider">GALAXY SECTOR</span>
                    <span className="inline-flex items-center justify-between text-[#1A1A1A] font-black text-[10px]">
                      <span>C-{activeNode.community}</span>
                      <span className="text-[6.5px] px-1.5 py-0.5 border border-current/25 bg-current/5 rounded-sm font-bold tracking-widest text-[#1A1A1A]/80">ZONE</span>
                    </span>
                  </div>
                </div>

                {/* Traversal Integration lists (Genuinely Interactive) */}
                {selectedNode && (
                  <div className="space-y-3 border-t border-current/10 pt-3 text-[9px] uppercase tracking-wide">
                    {/* Dependencies (Imports) */}
                    <div>
                      <div className="flex justify-between items-center opacity-65 font-bold text-[7.5px] tracking-wider mb-1.5">
                        <span>GRAVITATIONAL INFLOW (IMPORTS)</span>
                        <span className="text-blue-500 font-mono">[{activeNodeRelations.imports.length}]</span>
                      </div>
                      {activeNodeRelations.imports.length > 0 ? (
                        <div className="grid grid-cols-1 gap-1">
                          {activeNodeRelations.imports.map((n) => (
                            <button
                              key={n.id}
                              onClick={() => handleSelectSearched(n)}
                              className="text-left font-bold text-[8.5px] border border-blue-500/15 hover:border-blue-500 hover:bg-blue-500/5 px-2 py-1 truncate flex items-center justify-between group transition-all text-[#1A1A1A] cursor-pointer"
                              title={`Focus on ${n.name}`}
                            >
                              <span className="truncate">→ {n.name}</span>
                              <span className="text-blue-500 opacity-60 group-hover:opacity-100 font-mono text-[7px]">TRAVEL</span>
                            </button>
                          ))}
                        </div>
                      ) : (
                        <div className="border border-current/10 border-dashed text-zinc-400 text-[8px] py-1.5 text-center font-bold">
                          [ISOLATED SOURCE VECTOR]
                        </div>
                      )}
                    </div>

                    {/* Dependents (Used By / Exports) */}
                    <div>
                      <div className="flex justify-between items-center opacity-65 font-bold text-[7.5px] tracking-wider mb-1.5">
                        <span>GRAVITATIONAL OUTFLOW (EXPORTS)</span>
                        <span className="text-emerald-500 font-mono">[{activeNodeRelations.exports.length}]</span>
                      </div>
                      {activeNodeRelations.exports.length > 0 ? (
                        <div className="grid grid-cols-1 gap-1">
                          {activeNodeRelations.exports.map((n) => (
                            <button
                              key={n.id}
                              onClick={() => handleSelectSearched(n)}
                              className="text-left font-bold text-[8.5px] border border-emerald-500/15 hover:border-emerald-500 hover:bg-emerald-500/5 px-2 py-1 truncate flex items-center justify-between group transition-all text-[#1A1A1A] cursor-pointer"
                              title={`Focus on ${n.name}`}
                            >
                              <span className="truncate">← {n.name}</span>
                              <span className="text-emerald-500 opacity-60 group-hover:opacity-100 font-mono text-[7px]">TRAVEL</span>
                            </button>
                          ))}
                        </div>
                      ) : (
                        <div className="border border-current/10 border-dashed text-zinc-400 text-[8px] py-1.5 text-center font-bold">
                          [LEAF CORNER SATELLITE]
                        </div>
                      )}
                    </div>
                  </div>
                )}

                <div className="pt-2.5 border-t border-current/10 flex flex-col gap-1 text-[7.5px] opacity-75 tracking-wider uppercase">
                  <div className="flex justify-between items-center">
                    <span className="flex items-center gap-1">
                      <Sliders size={8} /> POSITION MATRIX
                    </span>
                    <span className="font-bold text-[#1A1A1A]">
                      X:{Math.round(activeNode.x)}  Y:{Math.round(activeNode.y)}
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-[7px] opacity-60">
                    <span>SIGNAL STABILITY</span>
                    <span>📶 98.4% SECURE</span>
                  </div>
                </div>
              </div>

              {/* Close source warning tag details */}
              {(activeNode.group === "core" || activeNode.group === "tools") ? (
                <div className="mt-1 flex items-center gap-2 border border-[#ef4444]/30 bg-[#ef4444]/5 px-2.5 py-1.5 text-[8.5px] uppercase tracking-wider text-[#ef4444] font-bold">
                  <ShieldAlert size={12} className="animate-pulse shrink-0" />
                  <span>RESTRICTED MODULE: COMPILED BINARY CONTROL</span>
                </div>
              ) : (
                <div className="mt-1 flex items-center gap-2 border border-[#1A1A1A]/10 bg-[#1A1A1A]/5 px-2.5 py-1.5 text-[8.5px] uppercase tracking-wider text-zinc-700 font-bold">
                  <Cpu size={12} className="shrink-0" />
                  <span>TARGET LOCK SECURED & INTEGRATED</span>
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
