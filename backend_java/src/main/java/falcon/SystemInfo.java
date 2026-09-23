package falcon;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Best-effort local hardware detection and resource sampling for the info
 * badge (mirrors backend/system_info.py).
 */
public final class SystemInfo {
    private SystemInfo() {}

    private static final int PROBE_TIMEOUT_S = 5;
    private static final String OS = System.getProperty("os.name", "").toLowerCase(Locale.ROOT);
    private static final boolean LINUX = OS.contains("linux");
    private static final boolean MAC = OS.contains("mac") || OS.contains("darwin");

    /** Runs a command, returns its stdout or null on any failure. */
    static String run(String... cmd) {
        try {
            Process p = new ProcessBuilder(cmd).redirectErrorStream(false).start();
            StringBuilder sb = new StringBuilder();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = r.readLine()) != null) sb.append(line).append('\n');
            }
            if (!p.waitFor(PROBE_TIMEOUT_S, TimeUnit.SECONDS)) {
                p.destroyForcibly();
                return null;
            }
            return p.exitValue() == 0 ? sb.toString() : null;
        } catch (IOException e) {
            return null;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return null;
        }
    }

    static boolean which(String exe) {
        String path = System.getenv("PATH");
        if (path == null) return false;
        for (String dir : path.split(java.io.File.pathSeparator)) {
            Path p = Path.of(dir, exe);
            if (Files.isExecutable(p)) return true;
        }
        return false;
    }

    private static List<Map<String, Object>> detectNvidiaGpus() {
        List<Map<String, Object>> gpus = new ArrayList<>();
        if (!which("nvidia-smi")) return gpus;
        String out = run("nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits");
        if (out == null) return gpus;
        for (String line : Py.strip(out).split("\n")) {
            String[] parts = line.split(",");
            if (parts.length < 3) continue;
            int index;
            try {
                index = Integer.parseInt(Py.strip(parts[0]));
            } catch (NumberFormatException e) {
                continue;
            }
            Integer vram;
            try {
                vram = (int) Double.parseDouble(Py.strip(parts[2]));
            } catch (NumberFormatException e) {
                vram = null;
            }
            gpus.add(Json.obj("index", index, "name", Py.strip(parts[1]), "vram_mb", vram, "roles", new ArrayList<>()));
        }
        return gpus;
    }

    private static List<Map<String, Object>> detectAppleGpu() {
        List<Map<String, Object>> gpus = new ArrayList<>();
        if (!MAC) return gpus;
        String out = run("system_profiler", "SPDisplaysDataType");
        if (out == null) return gpus;
        Matcher m = Pattern.compile("Chipset Model:\\s*(.+)").matcher(out);
        if (!m.find()) return gpus;
        Integer vram = null;
        String mem = run("sysctl", "-n", "hw.memsize");
        if (mem != null) {
            try {
                vram = (int) (Long.parseLong(Py.strip(mem)) / (1024 * 1024));
            } catch (NumberFormatException ignored) { }
        }
        gpus.add(Json.obj("index", 0, "name", Py.strip(m.group(1)), "vram_mb", vram, "roles", new ArrayList<>()));
        return gpus;
    }

    private static Integer detectRamMb() {
        if (LINUX) {
            try {
                for (String line : Files.readAllLines(Path.of("/proc/meminfo"))) {
                    if (line.startsWith("MemTotal:")) return (int) (Long.parseLong(line.split("\\s+")[1]) / 1024);
                }
            } catch (IOException | RuntimeException e) {
                return null;
            }
            return null;
        }
        if (MAC) {
            String mem = run("sysctl", "-n", "hw.memsize");
            if (mem == null) return null;
            try {
                return (int) (Long.parseLong(Py.strip(mem)) / (1024 * 1024));
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return null;
    }

    private static long[] prevCpuTimes;

    private static long[] readProcStatCpuTimes() {
        try (BufferedReader r = Files.newBufferedReader(Path.of("/proc/stat"))) {
            String line = r.readLine();
            if (line == null) return null;
            String[] parts = line.trim().split("\\s+");
            if (parts.length < 5 || !parts[0].equals("cpu")) return null;
            long total = 0;
            long[] nums = new long[parts.length - 1];
            for (int i = 1; i < parts.length; i++) {
                nums[i - 1] = Long.parseLong(parts[i]);
                total += nums[i - 1];
            }
            long idle = nums[3] + (nums.length > 4 ? nums[4] : 0);
            return new long[]{idle, total};
        } catch (IOException | RuntimeException e) {
            return null;
        }
    }

    private static Double cpuPercentLinux() {
        long[] cur = readProcStatCpuTimes();
        if (cur == null) return null;
        long[] prev;
        synchronized (SystemInfo.class) {
            prev = prevCpuTimes;
            prevCpuTimes = cur;
        }
        if (prev == null) return null;
        long dIdle = cur[0] - prev[0];
        long dTotal = cur[1] - prev[1];
        if (dTotal <= 0) return null;
        return Math.max(0.0, Math.min(100.0, 100.0 * (1 - (double) dIdle / dTotal)));
    }

    private static Double cpuPercentMac() {
        double load = ManagementFactory.getOperatingSystemMXBean().getSystemLoadAverage();
        if (load < 0) return null;
        int count = Runtime.getRuntime().availableProcessors();
        return Math.max(0.0, Math.min(100.0, 100.0 * load / count));
    }

    private static List<Object> nvidiaGpuUtilization() {
        List<Object> result = new ArrayList<>();
        if (!which("nvidia-smi")) return result;
        String out = run("nvidia-smi", "--query-gpu=index,utilization.gpu", "--format=csv,noheader,nounits");
        if (out == null) return result;
        for (String line : Py.strip(out).split("\n")) {
            String[] parts = line.split(",");
            if (parts.length < 2) continue;
            try {
                int index = Integer.parseInt(Py.strip(parts[0]));
                double pct = Double.parseDouble(Py.strip(parts[1]));
                result.add(Json.obj("index", index, "percent", Math.max(0.0, Math.min(100.0, pct))));
            } catch (NumberFormatException ignored) { }
        }
        return result;
    }

    /** {"cpu_percent": Double|null, "gpu_percent": [{"index","percent"}]} */
    public static Map<String, Object> sampleResourceUsage() {
        Double cpu = LINUX ? cpuPercentLinux() : MAC ? cpuPercentMac() : null;
        return Json.obj("cpu_percent", cpu, "gpu_percent", nvidiaGpuUtilization());
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> getSystemInfo(String llmModel, String interactiveLlmModel, String embedModel,
                                                    boolean embedOnGpu) {
        int cpuCount = Runtime.getRuntime().availableProcessors();
        Integer ram = detectRamMb();
        boolean forceCpu = !Env.get("LLAMA_FORCE_CPU", "").isEmpty();
        boolean unified = false;
        List<Map<String, Object>> gpus = detectNvidiaGpus();
        if (gpus.isEmpty()) {
            gpus = detectAppleGpu();
            unified = !gpus.isEmpty();
        }
        List<Object> cpuRoles = new ArrayList<>();
        final List<Map<String, Object>> g = gpus;
        java.util.function.IntFunction<Map<String, Object>> gpuByIndex = idx -> {
            for (Map<String, Object> gpu : g) if (((Number) gpu.get("index")).intValue() == idx) return gpu;
            return g.isEmpty() ? null : g.get(0);
        };
        int primary;
        try {
            String raw = Py.strip(Env.get("LLM_GPU_INDEX", "0"));
            primary = Integer.parseInt(raw.isEmpty() ? "0" : raw);
        } catch (NumberFormatException e) {
            primary = 0;
        }
        java.util.function.BiConsumer<Map<String, Object>, Integer> assign = (entry, idx) -> {
            Map<String, Object> gpu = (idx != null && !g.isEmpty()) ? gpuByIndex.apply(idx) : null;
            if (gpu != null) ((List<Object>) gpu.get("roles")).add(entry);
            else cpuRoles.add(entry);
        };
        assign.accept(Json.obj("kind", "llm_auto", "model", llmModel), forceCpu ? null : primary);
        if (interactiveLlmModel != null) {
            String raw = Py.strip(Env.get("LLM_INTERACTIVE_GPU_INDEX", ""));
            int ii = primary;
            if (!raw.isEmpty()) {
                try {
                    ii = Integer.parseInt(raw);
                } catch (NumberFormatException ignored) { }
            }
            assign.accept(Json.obj("kind", "llm_interactive", "model", interactiveLlmModel), forceCpu ? null : ii);
        }
        if (embedModel != null) {
            if (embedOnGpu && !gpus.isEmpty()) {
                assign.accept(Json.obj("kind", "embedding", "model", embedModel),
                        ((Number) gpus.get(0).get("index")).intValue());
            } else {
                cpuRoles.add(Json.obj("kind", "embedding", "model", embedModel));
            }
        }
        return Json.obj("gpus", gpus, "cpu_roles", cpuRoles, "unified_memory", unified, "cpu_count", cpuCount,
                "ram_total_mb", ram, "llm_model", llmModel, "interactive_llm_model", interactiveLlmModel,
                "embed_model", embedModel);
    }
}
