using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace LeakageSimulatorDesktop
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new LauncherForm());
        }
    }

    internal sealed class LauncherForm : Form
    {
        private const int StartupTimeoutSeconds = 180;
        private readonly Label _statusLabel;
        private readonly Label _hintLabel;
        private readonly Panel _headerPanel;
        private readonly WebView2 _webView;
        private readonly TextBox _logBox;
        private Process _serverProcess;
        private string _appRoot;
        private string _webUrl;
        private string _lastLogs = string.Empty;
        private string _launcherLogPath;

        public LauncherForm()
        {
            Text = "Leakage Simulator Desktop";
            Width = 1680;
            Height = 980;
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(1280, 820);
            BackColor = Color.FromArgb(14, 22, 38);

            _headerPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 66,
                Padding = new Padding(18, 12, 18, 12),
                BackColor = Color.FromArgb(18, 28, 46),
            };

            _statusLabel = new Label
            {
                Dock = DockStyle.Top,
                Height = 24,
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 12f, FontStyle.Bold),
                Text = "Starting React desktop UI...",
            };

            _hintLabel = new Label
            {
                Dock = DockStyle.Bottom,
                Height = 18,
                ForeColor = Color.FromArgb(164, 181, 214),
                Font = new Font("Segoe UI", 9f, FontStyle.Regular),
                Text = "Please wait. STEP/STP import and ray tracing are included in this desktop package.",
            };

            _headerPanel.Controls.Add(_hintLabel);
            _headerPanel.Controls.Add(_statusLabel);

            _webView = new WebView2
            {
                Dock = DockStyle.Fill,
                Visible = false,
                DefaultBackgroundColor = Color.FromArgb(11, 17, 30),
            };

            _logBox = new TextBox
            {
                Dock = DockStyle.Fill,
                Multiline = true,
                ReadOnly = true,
                BorderStyle = BorderStyle.None,
                BackColor = Color.FromArgb(11, 17, 30),
                ForeColor = Color.FromArgb(198, 214, 245),
                Font = new Font("Consolas", 10f, FontStyle.Regular),
                ScrollBars = ScrollBars.Vertical,
                Text =
                    "Desktop launcher is preparing the local server..." + Environment.NewLine +
                    "If startup fails, recent logs will appear here.",
            };

            Controls.Add(_logBox);
            Controls.Add(_webView);
            Controls.Add(_headerPanel);

            Shown += async (sender, args) => await StartAsync();
            FormClosing += OnFormClosing;
        }

        private async Task StartAsync()
        {
            try
            {
                _appRoot = ResolveAppRoot();
                _launcherLogPath = Path.Combine(_appRoot, "desktop_runtime", "launcher.log");
                Directory.CreateDirectory(Path.GetDirectoryName(_launcherLogPath));
                File.WriteAllText(_launcherLogPath, string.Empty);
                EnsureRuntimeFiles(_appRoot);
                int port = FindFreePort(8787, 40);
                _webUrl = "http://127.0.0.1:" + port;

                AppendLog("[INFO] App root: " + _appRoot);
                AppendLog("[INFO] Selected port: " + port);

                StartServerProcess(_appRoot, port);
                UpdateStatus("Starting local simulation server...");

                bool ok = await WaitForHealthAsync(_webUrl + "/health", StartupTimeoutSeconds);
                if (!ok)
                {
                    throw new InvalidOperationException(
                        BuildStartupFailureMessage()
                    );
                }

                UpdateStatus("Opening simulator...");
                try
                {
                    await InitializeWebViewAsync();
                    _webView.Source = new Uri(_webUrl);
                    _webView.Visible = true;
                    _logBox.Visible = false;
                    UpdateStatus("Leakage simulator ready");
                    _hintLabel.Text = "Desktop mode: no browser command needed. Just keep this window open.";
                }
                catch (Exception webViewError)
                {
                    AppendLog("[WARN] Embedded WebView2 failed: " + webViewError.Message);
                    Process.Start(_webUrl);
                    UpdateStatus("Simulator opened in the default browser");
                    _hintLabel.Text = "Keep this launcher open while using the simulator.";
                }
            }
            catch (Exception ex)
            {
                TryWriteLog("[ERR] " + ex);
                _webView.Visible = false;
                _logBox.Visible = true;
                _logBox.Text = ex.ToString();
                UpdateStatus("Startup failed");
                _hintLabel.Text = "Please review the message below.";
                MessageBox.Show(
                    this,
                    ex.Message,
                    "Leakage Simulator Desktop",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
            }
        }

        private static string ResolveAppRoot()
        {
            string exeDir = AppDomain.CurrentDomain.BaseDirectory;
            if (File.Exists(Path.Combine(exeDir, "run_web.py")))
            {
                return exeDir;
            }

            string candidate = Path.GetFullPath(Path.Combine(exeDir, ".."));
            if (File.Exists(Path.Combine(candidate, "run_web.py")))
            {
                return candidate;
            }

            throw new FileNotFoundException("run_web.py was not found next to the desktop launcher.");
        }

        private static void EnsureRuntimeFiles(string appRoot)
        {
            string pythonExe = Path.Combine(appRoot, "_tools", "python313", "python.exe");
            string runWeb = Path.Combine(appRoot, "run_web.py");
            string runApi = Path.Combine(appRoot, "run_api.py");
            string frontendIndex = Path.Combine(appRoot, "frontend", "dist", "index.html");
            if (!File.Exists(pythonExe))
            {
                throw new FileNotFoundException("Embedded Python was not found.", pythonExe);
            }

            if (!File.Exists(runWeb))
            {
                throw new FileNotFoundException("run_web.py was not found.", runWeb);
            }
            if (!File.Exists(runApi))
            {
                throw new FileNotFoundException("run_api.py was not found.", runApi);
            }
            if (!File.Exists(frontendIndex))
            {
                throw new FileNotFoundException("React production UI was not found.", frontendIndex);
            }
        }

        private void StartServerProcess(string appRoot, int port)
        {
            string pythonExe = Path.Combine(appRoot, "_tools", "python313", "python.exe");
            string runWeb = Path.Combine(appRoot, "run_web.py");

            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = pythonExe,
                Arguments = "-u \"" + runWeb + "\" --port " + port + " --strict-port",
                WorkingDirectory = appRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            psi.EnvironmentVariables["LEAKAGE_DESKTOP_BOOT"] = "1";

            _serverProcess = new Process { StartInfo = psi, EnableRaisingEvents = true };
            _serverProcess.OutputDataReceived += OnServerOutput;
            _serverProcess.ErrorDataReceived += OnServerOutput;
            _serverProcess.Exited += (sender, args) =>
            {
                AppendLog("[INFO] Server process exited.");
            };

            if (!_serverProcess.Start())
            {
                throw new InvalidOperationException("Failed to start embedded Python server.");
            }

            AppendLog("[INFO] Embedded Python: " + pythonExe);
            AppendLog("[INFO] Server PID: " + _serverProcess.Id);
            _serverProcess.BeginOutputReadLine();
            _serverProcess.BeginErrorReadLine();
        }

        private string BuildStartupFailureMessage()
        {
            string processState = "The embedded Python process is still running but localhost did not respond.";
            try
            {
                if (_serverProcess != null && _serverProcess.HasExited)
                {
                    processState = "The embedded Python process exited early (exit code " + _serverProcess.ExitCode + ").";
                }
            }
            catch
            {
            }

            return
                "The local web server did not become ready within " + StartupTimeoutSeconds + " seconds." +
                Environment.NewLine + processState +
                Environment.NewLine + Environment.NewLine +
                "Likely causes: company endpoint security/antivirus delayed or blocked the embedded Python/CAD runtime, or localhost access was restricted." +
                Environment.NewLine + "Diagnostic log: " + _launcherLogPath +
                Environment.NewLine + Environment.NewLine + _lastLogs;
        }

        private void OnServerOutput(object sender, DataReceivedEventArgs args)
        {
            if (string.IsNullOrWhiteSpace(args.Data))
            {
                return;
            }

            AppendLog(args.Data);
        }

        private void AppendLog(string line)
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action<string>(AppendLog), line);
                return;
            }

            if (_logBox.TextLength > 0)
            {
                _logBox.AppendText(Environment.NewLine);
            }

            _logBox.AppendText(line);
            _lastLogs = (_lastLogs + Environment.NewLine + line).Trim();
            TryWriteLog(line);
            if (_lastLogs.Length > 5000)
            {
                _lastLogs = _lastLogs.Substring(_lastLogs.Length - 5000);
            }
        }

        private void TryWriteLog(string line)
        {
            try
            {
                if (!string.IsNullOrWhiteSpace(_launcherLogPath))
                {
                    File.AppendAllText(_launcherLogPath, line + Environment.NewLine, Encoding.UTF8);
                }
            }
            catch
            {
            }
        }

        private void UpdateStatus(string text)
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action<string>(UpdateStatus), text);
                return;
            }

            _statusLabel.Text = text;
        }

        private async Task InitializeWebViewAsync()
        {
            string userDataDir = Path.Combine(_appRoot, ".webview2");
            Directory.CreateDirectory(userDataDir);
            CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(null, userDataDir);
            await _webView.EnsureCoreWebView2Async(environment);
            _webView.CoreWebView2.Settings.AreDevToolsEnabled = false;
            _webView.CoreWebView2.Settings.IsStatusBarEnabled = false;
            _webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
            _webView.CoreWebView2.NavigationStarting += (sender, args) =>
            {
                UpdateStatus("Loading " + args.Uri);
            };
            _webView.CoreWebView2.NavigationCompleted += (sender, args) =>
            {
                UpdateStatus(args.IsSuccess ? "Leakage simulator ready" : "Navigation failed");
            };
        }

        private async Task<bool> WaitForHealthAsync(string url, int timeoutSeconds)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(timeoutSeconds);
            DateTime nextProgress = DateTime.UtcNow.AddSeconds(10);
            while (DateTime.UtcNow < deadline)
            {
                if (_serverProcess != null && _serverProcess.HasExited)
                {
                    AppendLog("[ERR] Server process exited before health check succeeded. Exit code: " + _serverProcess.ExitCode);
                    return false;
                }

                bool ok = false;
                try
                {
                    HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
                    request.Method = "GET";
                    request.Timeout = 2000;
                    using (HttpWebResponse response = (HttpWebResponse)await request.GetResponseAsync())
                    {
                        if (response.StatusCode == HttpStatusCode.OK)
                        {
                            ok = true;
                        }
                    }
                }
                catch
                {
                }

                if (ok)
                {
                    AppendLog("[INFO] Local server health check succeeded.");
                    return true;
                }

                if (DateTime.UtcNow >= nextProgress)
                {
                    int elapsedSeconds = StartupTimeoutSeconds - Math.Max(0, (int)(deadline - DateTime.UtcNow).TotalSeconds);
                    AppendLog("[INFO] Still preparing local server... " + elapsedSeconds + "s elapsed");
                    UpdateStatus("Preparing CAD runtime and local server... " + elapsedSeconds + "s");
                    nextProgress = DateTime.UtcNow.AddSeconds(10);
                }

                await Task.Delay(600);
            }

            return false;
        }

        private static int FindFreePort(int startPort, int tries)
        {
            for (int i = 0; i < tries; i++)
            {
                int candidate = startPort + i;
                TcpListener listener = null;
                try
                {
                    listener = new TcpListener(System.Net.IPAddress.Loopback, candidate);
                    listener.Start();
                    return candidate;
                }
                catch
                {
                }
                finally
                {
                    if (listener != null)
                    {
                        listener.Stop();
                    }
                }
            }

            throw new InvalidOperationException("No free localhost port was found.");
        }

        private void OnFormClosing(object sender, FormClosingEventArgs args)
        {
            try
            {
                if (_serverProcess != null && !_serverProcess.HasExited)
                {
                    _serverProcess.Kill();
                    _serverProcess.WaitForExit(3000);
                }
            }
            catch
            {
            }
        }
    }
}
