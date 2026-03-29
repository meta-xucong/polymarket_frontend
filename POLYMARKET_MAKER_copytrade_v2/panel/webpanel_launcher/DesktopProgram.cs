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
            string runtimeExe = Path.Combine(baseDir, "desktop_runtime", "PolymarketDesktopRuntime.exe");
            if (!File.Exists(runtimeExe))
            {
                return;
            }

            var startInfo = new ProcessStartInfo
            {
                FileName = runtimeExe,
                WorkingDirectory = Path.GetDirectoryName(runtimeExe) ?? baseDir,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            startInfo.EnvironmentVariables["POLY_DESKTOP_APP_MODE"] = "desktop";
            startInfo.EnvironmentVariables["POLY_APP_ROOT"] = Path.Combine(baseDir, "app_root");
            startInfo.EnvironmentVariables["POLY_DESKTOP_BIN_DIR"] = Path.Combine(baseDir, "bin");

            Process.Start(startInfo);
        }
    }
}
