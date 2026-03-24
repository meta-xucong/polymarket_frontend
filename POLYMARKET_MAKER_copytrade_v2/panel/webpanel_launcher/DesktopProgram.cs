using System;
using System.Diagnostics;
using System.IO;

namespace PolymarketDesktopLauncher
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string runtimeExe = Path.Combine(baseDir, "webpanel_runtime", "PolymarketWebPanel.exe");
            string logPath = Path.Combine(baseDir, "desktop_launcher_root.log");

            void Log(string message)
            {
                try
                {
                    File.AppendAllText(logPath, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {message}{Environment.NewLine}");
                }
                catch
                {
                }
            }

            Log("desktop root launcher start");
            if (!File.Exists(runtimeExe))
            {
                Log($"missing runtime exe: {runtimeExe}");
                return;
            }

            string appRoot = Path.Combine(baseDir, "app_root");
            string binDir = Path.Combine(baseDir, "bin");
            var startInfo = new ProcessStartInfo
            {
                FileName = runtimeExe,
                WorkingDirectory = Path.GetDirectoryName(runtimeExe) ?? baseDir,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            startInfo.EnvironmentVariables["POLY_DESKTOP_APP_MODE"] = "browser";
            startInfo.EnvironmentVariables["POLY_DESKTOP_FORCE_BROWSER"] = "1";
            startInfo.EnvironmentVariables["POLY_BROWSER_IDLE_TIMEOUT_SEC"] = "86400";
            startInfo.EnvironmentVariables["POLY_BROWSER_IDLE_GRACE_SEC"] = "120";
            startInfo.EnvironmentVariables["POLY_APP_ROOT"] = appRoot;
            startInfo.EnvironmentVariables["POLY_INSTANCE_ROOT"] = appRoot;
            startInfo.EnvironmentVariables["POLY_DESKTOP_BIN_DIR"] = binDir;

            try
            {
                Process? child = Process.Start(startInfo);
                Log(child is null ? "runtime start returned null" : $"runtime pid={child.Id}");
                child?.WaitForExit();
            }
            catch (Exception ex)
            {
                Log($"launch failed: {ex}");
            }
        }
    }
}
