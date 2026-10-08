import Sortable from "sortablejs";
import {
  Chart,
  BarController,
  BarElement,
  DoughnutController,
  ArcElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend
} from "chart.js";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Ban,
  BarChart3,
  Bell,
  Briefcase,
  Building2,
  CalendarDays,
  CalendarPlus,
  ChartColumn,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CirclePause,
  CircleUser,
  CircleX,
  Clock3,
  Contact,
  Copy,
  ClipboardCheck,
  Columns3,
  createIcons,
  Download,
  ExternalLink,
  FileCheck2,
  FileDiff,
  FileText,
  Filter,
  GripVertical,
  Info,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  Link2,
  LockKeyhole,
  LogOut,
  Mail,
  MapPin,
  Merge,
  Menu,
  PanelLeft,
  Phone,
  Play,
  Plus,
  RefreshCw,
  ScanText,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  ScrollText,
  Sparkles,
  TrendingUp,
  TriangleAlert,
  UserPlus,
  Users,
  Video,
  X
} from "lucide";

Chart.register(
  BarController,
  BarElement,
  DoughnutController,
  ArcElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend
);

const iconSet = {
  Activity,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Ban,
  BarChart3,
  Bell,
  Briefcase,
  Building2,
  CalendarDays,
  CalendarPlus,
  ChartColumn,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CirclePause,
  CircleUser,
  CircleX,
  Clock3,
  Contact,
  Copy,
  ClipboardCheck,
  Columns3,
  Download,
  ExternalLink,
  FileCheck2,
  FileDiff,
  FileText,
  Filter,
  GripVertical,
  Info,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  Link2,
  LockKeyhole,
  LogOut,
  Mail,
  MapPin,
  Merge,
  Menu,
  PanelLeft,
  Phone,
  Play,
  Plus,
  RefreshCw,
  ScanText,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  ScrollText,
  Sparkles,
  TrendingUp,
  TriangleAlert,
  UserPlus,
  Users,
  Video,
  X
};

const PALETTE = {
  brand: "#2563eb",
  brandDark: "#1d4ed8",
  emerald: "#16a34a",
  amber: "#d97706",
  sky: "#0284c7",
  slate: "#64748b",
  grid: "#e2e8f0",
  ink: "#475569"
};

const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function renderIcons(root = document) {
  createIcons({
    icons: iconSet,
    attrs: {
      "aria-hidden": "true",
      "stroke-width": "1.8"
    },
    nameAttr: "data-lucide",
    root
  });
}

/* ------------------------------------------------------------------ toasts */

function toastStack() {
  let stack = document.querySelector(".toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toast-stack";
    stack.setAttribute("role", "status");
    stack.setAttribute("aria-live", "polite");
    document.body.appendChild(stack);
  }
  return stack;
}

function showToast(message, variant = "error") {
  if (!message) return;
  const el = document.createElement("div");
  el.className = `toast toast-${variant}`;
  el.textContent = message;
  toastStack().appendChild(el);
  window.setTimeout(() => el.remove(), 6000);
}

/* --------------------------------------------------------------- navigation */

function setupMobileNavigation() {
  const sidebar = document.querySelector("[data-mobile-nav]");
  const backdrop = document.querySelector("[data-mobile-nav-backdrop]");
  const toggle = document.querySelector("[data-mobile-nav-toggle]");
  const close = document.querySelector("[data-mobile-nav-close]");
  if (!sidebar || !backdrop || !toggle) return;

  let lastFocus = null;
  const desktopQuery = window.matchMedia("(min-width: 1024px)");
  sidebar.setAttribute("aria-hidden", String(!desktopQuery.matches));
  const setOpen = (open) => {
    sidebar.classList.toggle("is-open", open);
    backdrop.classList.toggle("is-open", open);
    backdrop.hidden = !open;
    sidebar.setAttribute("aria-hidden", String(!open));
    toggle.setAttribute("aria-expanded", String(open));
    document.body.classList.toggle("mobile-nav-open", open);
    if (open) {
      lastFocus = document.activeElement;
      close?.focus();
    } else {
      lastFocus?.focus?.();
      lastFocus = null;
    }
  };

  toggle.addEventListener("click", () => setOpen(true));
  close?.addEventListener("click", () => setOpen(false));
  backdrop.addEventListener("click", () => setOpen(false));
  sidebar.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => setOpen(false)));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sidebar.classList.contains("is-open")) setOpen(false);
  });
  desktopQuery.addEventListener("change", (event) => {
    sidebar.setAttribute("aria-hidden", String(!event.matches && !sidebar.classList.contains("is-open")));
    if (event.matches) setOpen(false);
  });
}

/* ------------------------------------------------------------------- forms */

function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || "";
}

function setupSubmitStates() {
  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector('button[type="submit"]:not([disabled])');
      if (!button) return;
      button.setAttribute("aria-busy", "true");
      window.setTimeout(() => button.setAttribute("disabled", "disabled"), 0);
    });
  });
}

function setupConfirmDialogs() {
  document.querySelectorAll("[data-confirm]").forEach((element) => {
    element.addEventListener("click", (event) => {
      if (!window.confirm(element.dataset.confirm)) event.preventDefault();
    });
  });
}

/* ------------------------------------------------------------------ kanban */

function setupKanban() {
  document.querySelectorAll("[data-kanban-stage]").forEach((column) => {
    if (column.dataset.canMove !== "true" || column.dataset.sortableReady === "true") return;
    column.dataset.sortableReady = "true";
    new Sortable(column, {
      group: "pipeline",
      animation: prefersReducedMotion ? 0 : 160,
      handle: "[data-drag-handle]",
      ghostClass: "opacity-40",
      onEnd: async (event) => {
        const card = event.item;
        const stageId = event.to.dataset.stageId;
        if (!stageId || stageId === card.dataset.stageId) {
          window.location.reload();
          return;
        }
        card.setAttribute("aria-busy", "true");
        const payload = {
          target_stage_id: stageId,
          expected_version: Number(card.dataset.version),
          reason: "Kanban drag and drop"
        };
        try {
          const response = await fetch(card.dataset.apiEndpoint, {
            method: "POST",
            credentials: "same-origin",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": csrfToken(),
              "Idempotency-Key": crypto.randomUUID()
            },
            body: JSON.stringify(payload)
          });
          if (!response.ok) {
            const responseText = await response.text();
            let message = responseText || "The application could not be moved.";
            try {
              message = JSON.parse(responseText)?.error?.message || message;
            } catch {
              // HTML/plain-text errors still provide a useful fallback message.
            }
            throw new Error(message);
          }
          window.location.reload();
        } catch (error) {
          showToast(error.message, "error");
          window.location.reload();
        }
      }
    });
  });
}

/* ------------------------------------------------------------------ charts */

function chartConfig(key, data) {
  const shared = {
    responsive: true,
    maintainAspectRatio: false,
    animation: prefersReducedMotion ? false : { duration: 500 },
    plugins: {
      legend: { display: false },
      tooltip: { displayColors: false }
    }
  };

  if (key === "funnel") {
    return {
      type: "bar",
      data: {
        labels: data.labels,
        datasets: [
          {
            label: "Applications",
            data: data.values,
            backgroundColor: PALETTE.brand,
            hoverBackgroundColor: PALETTE.brandDark,
            borderRadius: 4,
            maxBarThickness: 26
          }
        ]
      },
      options: {
        ...shared,
        indexAxis: "y",
        scales: {
          x: {
            beginAtZero: true,
            ticks: { precision: 0, color: PALETTE.ink, font: { size: 11 } },
            grid: { color: PALETTE.grid }
          },
          y: {
            ticks: { color: PALETTE.ink, font: { size: 11, weight: "600" } },
            grid: { display: false }
          }
        }
      }
    };
  }

  if (key === "timeInStage") {
    return {
      type: "bar",
      data: {
        labels: data.labels,
        datasets: [
          {
            label: "Average days",
            data: data.values,
            backgroundColor: PALETTE.sky,
            borderRadius: 4,
            maxBarThickness: 40
          }
        ]
      },
      options: {
        ...shared,
        scales: {
          x: {
            ticks: { color: PALETTE.ink, font: { size: 11, weight: "600" } },
            grid: { display: false }
          },
          y: {
            beginAtZero: true,
            ticks: { color: PALETTE.ink, font: { size: 11 } },
            grid: { color: PALETTE.grid }
          }
        }
      }
    };
  }

  return {
    type: "doughnut",
    data: {
      labels: data.labels,
      datasets: [
        {
          data: data.values,
          backgroundColor: [
            PALETTE.brand,
            PALETTE.sky,
            PALETTE.emerald,
            PALETTE.amber,
            PALETTE.slate
          ],
          borderWidth: 0,
          hoverOffset: 4
        }
      ]
    },
    options: {
      ...shared,
      cutout: "62%",
      plugins: {
        legend: {
          display: true,
          position: "bottom",
          labels: {
            boxWidth: 10,
            boxHeight: 10,
            usePointStyle: true,
            color: PALETTE.ink,
            font: { size: 11 }
          }
        },
        tooltip: { displayColors: false }
      }
    }
  };
}

function renderCharts(root = document) {
  root.querySelectorAll("canvas[data-chart]").forEach((canvas) => {
    const key = canvas.dataset.chart;
    const payload = document.querySelector(`script[data-chart-data="${key}"]`);
    if (!payload || canvas.dataset.chartReady === "true") return;
    let data;
    try {
      data = JSON.parse(payload.textContent);
    } catch {
      return;
    }
    if (!data || !data.labels || !data.labels.length) return;
    canvas.dataset.chartReady = "true";
    new Chart(canvas, chartConfig(key, data));
  });
}

/* -------------------------------------------------------------------- boot */

/**
 * Run one boot step without letting it take down the rest of the page.
 *
 * A failure in icon rendering must never stop the kanban board, the charts or the mobile
 * navigation from initialising.
 */
function boot(label, fn) {
  try {
    fn();
  } catch (error) {
    console.error(`[ats] ${label} failed to initialise`, error);
  }
}

boot("icons", () => renderIcons());
boot("mobile navigation", () => setupMobileNavigation());
boot("submit states", () => setupSubmitStates());
boot("confirm dialogs", () => setupConfirmDialogs());
boot("kanban", () => setupKanban());
boot("charts", () => renderCharts());
