// SparkBox Windows launcher: the single-file SparkBox.exe that users download.
//
// Purpose. Show a startup banner the instant the user clicks, run only one SparkBox per user, and start the real
// application (the PyInstaller folder embedded in this executable as a ZIP resource).
//
// How it works.
// 1. A second click while SparkBox is starting or open does not start anything: this launcher holds a per-user
//    mutex while it runs, and the application holds its own (app/startup.py). A second launcher sends "activate"
//    on the application's local channel (app/single_instance.py), so the open window comes to the front, and exits.
// 2. The banner (icon, name, current step, moving bar) is painted before any other work.
// 3. The embedded folder is extracted once per build to %LOCALAPPDATA%\SparkBox\app\<build id>; later starts reuse
//    it, so only the first start of a new version spends time unpacking. Older builds are removed.
// 4. The application writes its current step ("Loading gregorithm…") to a status file and creates a ready file
//    when its main window is on screen (SPARKBOX_STARTUP_STATUS / SPARKBOX_STARTUP_READY); the banner shows the
//    step and closes on ready. The launcher then exits; the application keeps running on its own.
// 5. Until a build has reached its main window once (marker file ".started" in its folder), the banner also says
//    that Windows is checking the files and that this first start can take a few minutes: antivirus scanning of
//    the newly extracted, unsigned libraries dominates that start. The banner then waits up to 15 minutes instead
//    of 3 before closing; the application keeps starting either way.
// 6. Right after extraction, before the application starts, the launcher asks the Windows shell for the icon of the
//    new SparkBox.exe (PrimeShellIcon). The taskbar draws the icon of the executable file; when its first read
//    happened while Windows was still checking the just-started file, the shell showed and kept a generic icon for
//    that whole session. Reading it first puts the real icon in the shell's cache.
//
// Build: tools/build_windows.py (needs the .NET Framework C# compiler that ships with Windows 10/11; C# 5 syntax).
// Limits: Windows only. Scientific processing is entirely in the Python application; this file only starts it.

using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.IO.Pipes;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

[assembly: AssemblyTitle("SparkBox")]
[assembly: AssemblyProduct("SparkBox")]
[assembly: AssemblyCompany("Alessandro Gregucci")]
[assembly: AssemblyCopyright("Copyright (c) 2026 Alessandro Gregucci")]

internal static class SparkBoxLauncher
{
    private const string AppName = "SparkBox";
    private static readonly Color Ink = Color.FromArgb(30, 41, 49);
    private static readonly Color Muted = Color.FromArgb(96, 110, 120);
    private static readonly Color Accent = Color.FromArgb(0, 109, 133);

    [DllImport("user32.dll")] private static extern bool AllowSetForegroundWindow(int processId);
    [DllImport("user32.dll")] private static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr icon);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr SHGetFileInfo(string path, uint attributes, ref ShellFileInfo info, uint size,
                                               uint flags);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern void SHChangeNotify(int eventId, uint flags, string item1, IntPtr item2);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct ShellFileInfo
    {
        public IntPtr icon;
        public int iconIndex;
        public uint attributes;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string displayName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 80)] public string typeName;
    }

    /// <summary>Ask the shell for the large and small icon of `exe` (and tell it the file is new), so the taskbar
    /// finds the real icon in the shell cache when the application window appears. Failures are ignored: the
    /// icon is cosmetic and the application must start anyway.</summary>
    private static void PrimeShellIcon(string exe)
    {
        const uint SHGFI_ICON = 0x100, SHGFI_LARGEICON = 0x0, SHGFI_SMALLICON = 0x1, SHGFI_SYSICONINDEX = 0x4000;
        const int SHCNE_UPDATEITEM = 0x2000;
        const uint SHCNF_PATHW = 0x5;
        try
        {
            SHChangeNotify(SHCNE_UPDATEITEM, SHCNF_PATHW, exe, IntPtr.Zero);
            foreach (uint size in new[] { SHGFI_LARGEICON, SHGFI_SMALLICON })
            {
                ShellFileInfo info = new ShellFileInfo();
                SHGetFileInfo(exe, 0, ref info, (uint)Marshal.SizeOf(info), SHGFI_ICON | SHGFI_SYSICONINDEX | size);
                if (info.icon != IntPtr.Zero) DestroyIcon(info.icon);
            }
        }
        catch (Exception) { }
    }

    /// <summary>Single-instance check, banner, then extraction and start in the background.</summary>
    [STAThread]
    private static int Main()
    {
        string channel = ChannelName();
        bool first;
        using (Mutex launcher = new Mutex(true, "Local\\" + channel + "-launcher", out first))
        {
            Mutex running;
            if (!first || Mutex.TryOpenExisting("Local\\" + channel, out running))
            {
                ActivateRunning(channel);          // SparkBox is starting or open: bring it forward, open nothing
                return 0;
            }
            SetProcessDPIAware();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Banner banner = new Banner();
            int exitCode = 0;
            banner.Shown += delegate
            {
                banner.Activate();
                banner.Update();
                Task.Run(delegate { exitCode = Start(banner); });
            };
            Application.Run(banner);
            return exitCode;
        }
    }

    /// <summary>Same per-user name as app.startup.channel_name(): "SparkBox-" + 16 hex digits of SHA-256(user).</summary>
    private static string ChannelName()
    {
        string user = null;
        foreach (string key in new[] { "LOGNAME", "USER", "LNAME", "USERNAME" })   // Python getpass.getuser() order
        {
            user = Environment.GetEnvironmentVariable(key);
            if (!String.IsNullOrEmpty(user)) break;
        }
        if (String.IsNullOrEmpty(user)) user = Environment.UserName;
        using (SHA256 sha = SHA256.Create())
        {
            byte[] hash = sha.ComputeHash(Encoding.UTF8.GetBytes(user.ToLowerInvariant()));
            StringBuilder hex = new StringBuilder("SparkBox-");
            for (int i = 0; i < 8; i++) hex.Append(hash[i].ToString("x2"));
            return hex.ToString();
        }
    }

    /// <summary>Ask the running application to show its window; give up silently after a few seconds.</summary>
    private static void ActivateRunning(string channel)
    {
        AllowSetForegroundWindow(-1);              // ASFW_ANY: the user's click belongs to this process
        DateTime deadline = DateTime.UtcNow.AddSeconds(5);
        while (DateTime.UtcNow < deadline)
        {
            try
            {
                using (NamedPipeClientStream pipe = new NamedPipeClientStream(".", channel, PipeDirection.Out))
                {
                    pipe.Connect(300);
                    byte[] message = Encoding.ASCII.GetBytes("activate");
                    pipe.Write(message, 0, message.Length);
                    pipe.Flush();
                    return;
                }
            }
            catch (Exception) { Thread.Sleep(200); }  // still starting (no channel yet): try again
        }
    }

    /// <summary>Extract if needed, start the application, follow its startup steps; returns the exit code.</summary>
    private static int Start(Banner banner)
    {
        string status = Path.Combine(Path.GetTempPath(), AppName + "-" + Guid.NewGuid().ToString("N") + ".status");
        string ready = Path.ChangeExtension(status, ".ready");
        try
        {
            string folder = ApplicationFolder();
            string started = Path.Combine(folder, ".started");
            bool firstStart = !File.Exists(started);
            if (firstStart)
                banner.ShowNote("First start of this version: Windows is checking SparkBox's files. "
                                + "This can take a few minutes; later starts are much faster.");
            PrepareApplication(banner, folder);
            string exe = Path.Combine(folder, AppName, AppName + ".exe");
            if (firstStart) PrimeShellIcon(exe);
            ProcessStartInfo command = new ProcessStartInfo(exe);
            command.UseShellExecute = false;
            command.WorkingDirectory = Path.Combine(folder, AppName);
            command.EnvironmentVariables["SPARKBOX_STARTUP_STATUS"] = status;
            command.EnvironmentVariables["SPARKBOX_STARTUP_READY"] = ready;
            banner.SetStep("Starting SparkBox…");
            using (Process app = Process.Start(command))
            {
                if (app == null) throw new IOException("Windows could not start the application.");
                AllowSetForegroundWindow(app.Id);
                DateTime deadline = DateTime.UtcNow.AddMinutes(firstStart ? 15 : 3);
                while (!File.Exists(ready) && !app.HasExited && DateTime.UtcNow < deadline)
                {
                    string step = ReadStep(status);
                    if (step != null) banner.SetStep(step);
                    app.WaitForExit(80);
                }
                if (firstStart && File.Exists(ready))
                {
                    try { File.WriteAllText(started, DateTime.UtcNow.ToString("o")); }
                    catch (Exception) { }          // not writable: the note simply appears again next time
                }
                if (app.HasExited && !File.Exists(ready) && app.ExitCode != 0)
                    throw new IOException("The application stopped during startup (exit code " + app.ExitCode + ").");
            }
            banner.Finish();
            return 0;
        }
        catch (Exception error)
        {
            banner.Fail(AppName + " could not start.\n\n" + error.Message);
            return 1;
        }
        finally
        {
            foreach (string path in new[] { status, status + ".tmp", ready })
            {
                try { File.Delete(path); } catch (Exception) { }
            }
        }
    }

    private static string ReadStep(string path)
    {
        try { return File.Exists(path) ? File.ReadAllText(path, Encoding.UTF8).Trim() : null; }
        catch (IOException) { return null; }      // being replaced right now: read it on the next tick
    }

    /// <summary>%LOCALAPPDATA%\SparkBox\app\&lt;build id&gt;: where this build's application lives once extracted.</summary>
    private static string ApplicationFolder()
    {
        string build;
        using (StreamReader reader = new StreamReader(
                   Assembly.GetExecutingAssembly().GetManifestResourceStream("sparkbox_build_id")))
            build = reader.ReadToEnd().Trim();
        string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), AppName, "app");
        return Path.Combine(root, build);
    }

    /// <summary>Extract this build's application into `folder` on its first start; older builds are removed.</summary>
    private static void PrepareApplication(Banner banner, string folder)
    {
        Assembly self = Assembly.GetExecutingAssembly();
        string root = Path.GetDirectoryName(folder);
        string build = Path.GetFileName(folder);
        if (File.Exists(Path.Combine(folder, ".complete"))) return;

        banner.SetStep("Preparing SparkBox (first start of this version)…");
        Directory.CreateDirectory(root);
        string partial = folder + ".partial-" + Guid.NewGuid().ToString("N");
        string prefix = Path.GetFullPath(partial) + Path.DirectorySeparatorChar;
        using (Stream payload = self.GetManifestResourceStream("sparkbox_payload"))
        using (ZipArchive archive = new ZipArchive(payload, ZipArchiveMode.Read))
        {
            int done = 0, total = archive.Entries.Count, shown = -1;
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                string target = Path.GetFullPath(Path.Combine(partial, entry.FullName));
                if (!target.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                    throw new IOException("The application archive contains an invalid path.");
                if (String.IsNullOrEmpty(entry.Name)) Directory.CreateDirectory(target);
                else
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    using (Stream input = entry.Open())
                    using (FileStream output = new FileStream(target, FileMode.CreateNew))
                        input.CopyTo(output, 81920);
                }
                int percent = (int)(100L * ++done / Math.Max(1, total));
                if (percent != shown)
                {
                    shown = percent;
                    banner.SetStep("Preparing SparkBox (first start of this version)… " + percent + "%");
                }
            }
        }
        File.WriteAllText(Path.Combine(partial, ".complete"), build);
        if (Directory.Exists(folder)) Directory.Delete(folder, true);      // an earlier, interrupted extraction
        Directory.Move(partial, folder);
        foreach (string old in Directory.GetDirectories(root))              // older builds and leftovers
        {
            if (String.Equals(old, folder, StringComparison.OrdinalIgnoreCase)) continue;
            try { Directory.Delete(old, true); } catch (Exception) { }      // in use or locked: next time
        }
    }

    /// <summary>The startup banner: icon, name, current step and a moving bar; it cannot be closed by the user.</summary>
    private sealed class Banner : Form
    {
        private readonly Label step;
        private readonly Label note;
        private readonly int noteHeight;
        private bool closing;

        public Banner()
        {
            float scale;
            using (Graphics g = CreateGraphics()) scale = g.DpiX / 96f;
            Func<int, int> S = delegate(int value) { return (int)Math.Round(value * scale); };
            noteHeight = S(52);
            Text = AppName;
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;         // centred below, on the screen of the click
            ShowInTaskbar = true;
            TopMost = true;                        // short-lived: visible even when started without focus
            BackColor = Color.White;
            ClientSize = new Size(S(440), S(230));
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            Padding = new Padding(1);

            PictureBox logo = new PictureBox();
            using (Stream png = Assembly.GetExecutingAssembly().GetManifestResourceStream("sparkbox_logo"))
                logo.Image = Image.FromStream(png);
            logo.SizeMode = PictureBoxSizeMode.Zoom;
            logo.Bounds = new Rectangle(S(32), S(40), S(104), S(104));
            Label title = new Label();
            title.Text = AppName;
            title.Font = new Font("Segoe UI Semibold", 24f, FontStyle.Regular, GraphicsUnit.Point);
            title.ForeColor = Ink;
            title.AutoSize = true;
            title.Location = new Point(S(156), S(52));
            Label engine = new Label();
            engine.Text = "Battery data analysis · gregorithm engine";
            engine.Font = new Font("Segoe UI", 9.5f, FontStyle.Regular, GraphicsUnit.Point);
            engine.ForeColor = Muted;
            engine.AutoSize = true;
            engine.Location = new Point(S(159), S(100));
            step = new Label();
            step.Text = "Opening…";
            step.Font = new Font("Segoe UI", 9.5f, FontStyle.Regular, GraphicsUnit.Point);
            step.ForeColor = Ink;
            step.AutoEllipsis = true;
            step.Bounds = new Rectangle(S(32), S(166), S(376), S(22));
            ProgressBar bar = new ProgressBar();
            bar.Style = ProgressBarStyle.Marquee;
            bar.MarqueeAnimationSpeed = 25;
            bar.Bounds = new Rectangle(S(32), S(194), S(376), S(6));
            note = new Label();                    // first-start explanation, hidden until ShowNote
            note.Font = new Font("Segoe UI", 9f, FontStyle.Regular, GraphicsUnit.Point);
            note.ForeColor = Muted;
            note.Bounds = new Rectangle(S(32), S(214), S(376), S(40));
            note.Visible = false;
            Controls.AddRange(new Control[] { logo, title, engine, step, bar, note });
            FormClosing += delegate(object sender, FormClosingEventArgs e) { if (!closing) e.Cancel = true; };
            Rectangle area = Screen.FromPoint(Cursor.Position).WorkingArea;
            Location = new Point(area.Left + (area.Width - Width) / 2, area.Top + (area.Height - Height) / 2);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            using (Pen border = new Pen(Color.FromArgb(214, 221, 226)))
                e.Graphics.DrawRectangle(border, 0, 0, ClientSize.Width - 1, ClientSize.Height - 1);
            using (SolidBrush band = new SolidBrush(Accent))
                e.Graphics.FillRectangle(band, 0, 0, ClientSize.Width, 4);
        }

        /// <summary>Show the current startup step (thread-safe).</summary>
        public void SetStep(string text)
        {
            if (IsDisposed) return;
            BeginInvoke(new Action(delegate { if (step.Text != text) step.Text = text; }));
        }

        /// <summary>Show an explanatory note below the bar, growing the banner to fit it (thread-safe, once).</summary>
        public void ShowNote(string text)
        {
            if (IsDisposed) return;
            BeginInvoke(new Action(delegate
            {
                note.Text = text;
                if (note.Visible) return;
                note.Visible = true;
                ClientSize = new Size(ClientSize.Width, ClientSize.Height + noteHeight);
                Top -= noteHeight / 2;             // stay centred where it was
                Invalidate();                      // repaint the border at the new size
            }));
        }

        /// <summary>Close the banner: the application window is on screen (thread-safe).</summary>
        public void Finish()
        {
            BeginInvoke(new Action(delegate { closing = true; Close(); }));
        }

        /// <summary>Show an error, then close (thread-safe).</summary>
        public void Fail(string message)
        {
            Invoke(new Action(delegate
            {
                MessageBox.Show(this, message, AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
                closing = true;
                Close();
            }));
        }
    }
}
