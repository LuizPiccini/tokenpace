// Token Pace Mini: a floating pill and a tray icon for Windows that show which AI plan to use first.
// It reads the same /api/widget feed as the Android widget. Builds with the C# compiler that ships
// with Windows (.NET Framework 4.8), so it needs no SDK: see build.ps1. C# 5 syntax on purpose.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using System.Windows.Threading;
using Microsoft.Win32;
using Forms = System.Windows.Forms;
using Gdi = System.Drawing;

namespace TokenPaceMini
{
    sealed class Plan
    {
        public string Group, Name, Window, Level, Verdict, Note;
        public int Rank;
        public double Used, Elapsed, Need;
        public bool Free, HasElapsed;
        public long Resets, Released;
    }

    sealed class GroupData
    {
        public string Label;
        public List<Plan> Items = new List<Plan>();
    }

    sealed class Snapshot
    {
        public List<GroupData> Groups = new List<GroupData>();
        public DateTime At;

        // The plan the pill shows: the first group's "use first", else its top row.
        public Plan Top()
        {
            foreach (var g in Groups)
            {
                foreach (var p in g.Items) if (p.Level == "use") return p;
                if (g.Items.Count > 0) return g.Items[0];
            }
            return null;
        }
    }

    sealed class ApiException : Exception
    {
        public ApiException(string message) : base(message) { }
    }

    static class Api
    {
        public static string Normalize(string s)
        {
            s = (s ?? "").Trim();
            if (s.Length == 0) return "";
            if (s.IndexOf("://", StringComparison.Ordinal) < 0) s = "http://" + s;
            s = s.TrimEnd('/');
            const string tail = "/api/widget";
            if (s.EndsWith(tail, StringComparison.OrdinalIgnoreCase)) s = s.Substring(0, s.Length - tail.Length);
            Uri uri;
            // Only web addresses: the value is later opened with the shell ("Open page").
            if (!Uri.TryCreate(s, UriKind.Absolute, out uri) || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps)) return "";
            return s;
        }

        const int MaxBody = 1024 * 1024;

        public static Snapshot Fetch(string server, string token)
        {
            if (!string.IsNullOrEmpty(token))
                foreach (char ch in token) if (ch < 33 || ch > 126) throw new ApiException("The token contains spaces or unusual characters");
            HttpWebRequest req;
            try { req = (HttpWebRequest)WebRequest.Create(server + "/api/widget"); }
            catch (Exception) { throw new ApiException("Not a valid server address"); }
            req.Timeout = 15000;
            req.ReadWriteTimeout = 15000;
            req.AllowAutoRedirect = false;   // never resend the token to another host
            req.Accept = "application/json";
            req.UserAgent = "TokenPaceMini/" + Program.Version;
            // Plain http would hand the token to a system proxy in clear text: go direct.
            if (req.RequestUri.Scheme == Uri.UriSchemeHttp) req.Proxy = null;
            if (!string.IsNullOrEmpty(token)) req.Headers[HttpRequestHeader.Authorization] = "Bearer " + token;
            bool timedOut = false;
            // ReadWriteTimeout is per read; this caps the whole exchange so a slow server can't stall refreshes.
            using (new Timer(delegate { timedOut = true; try { req.Abort(); } catch (Exception) { } }, null, 30000, Timeout.Infinite))
            try
            {
                using (var resp = (HttpWebResponse)req.GetResponse())
                {
                    int code = (int)resp.StatusCode;
                    if (code != 200) throw new ApiException("Server answered HTTP " + code);
                    if (resp.ContentLength > MaxBody) throw new ApiException("Server answer too large");
                    using (var stream = resp.GetResponseStream())
                    using (var ms = new MemoryStream())
                    {
                        var buf = new byte[16384];
                        int n;
                        while ((n = stream.Read(buf, 0, buf.Length)) > 0)
                        {
                            if (ms.Length + n > MaxBody) throw new ApiException("Server answer too large");
                            ms.Write(buf, 0, n);
                        }
                        return Parse(Encoding.UTF8.GetString(ms.ToArray()));
                    }
                }
            }
            catch (ApiException) { throw; }
            catch (WebException e)
            {
                if (timedOut) throw new ApiException("Server too slow to answer");
                var hr = e.Response as HttpWebResponse;
                if (hr != null)
                {
                    int code = (int)hr.StatusCode;
                    hr.Close();
                    if (code == 401) throw new ApiException("Wrong or missing token");
                    if (code == 421) throw new ApiException("Server refused this address: set a token or allowed_hosts");
                    throw new ApiException("Server answered HTTP " + code);
                }
                throw new ApiException("Can't reach the server");
            }
            catch (IOException)
            {
                throw new ApiException(timedOut ? "Server too slow to answer" : "Connection broke while reading");
            }
        }

        public static Snapshot Parse(string json)
        {
            var js = new JavaScriptSerializer();
            js.MaxJsonLength = 4 * 1024 * 1024;
            Dictionary<string, object> root;
            try { root = js.DeserializeObject(json) as Dictionary<string, object>; }
            catch (Exception) { throw new ApiException("The server did not answer with Token Pace data"); }
            if (root == null) throw new ApiException("The server did not answer with Token Pace data");
            var snap = new Snapshot();
            snap.At = DateTime.UtcNow;
            object groups;
            if (!root.TryGetValue("groups", out groups)) throw new ApiException("The server did not answer with Token Pace data");
            var list = groups as object[];
            if (list != null)
            {
                foreach (var o in list)
                {
                    var d = o as Dictionary<string, object>;
                    if (d == null) continue;
                    object items;
                    d.TryGetValue("items", out items);
                    AddGroup(snap, Str(d, "label") ?? Str(d, "id") ?? "", items as object[]);
                }
            }
            else
            {
                // Older private servers send {"Group": [rows]}.
                var dict = groups as Dictionary<string, object>;
                if (dict != null) foreach (var kv in dict) AddGroup(snap, kv.Key, kv.Value as object[]);
            }
            return snap;
        }

        static void AddGroup(Snapshot snap, string label, object[] items)
        {
            var g = new GroupData();
            g.Label = label;
            if (items != null)
            {
                foreach (var o in items)
                {
                    var d = o as Dictionary<string, object>;
                    if (d == null) continue;
                    var p = new Plan();
                    p.Group = label;
                    p.Rank = (int)Num(d, "rank", g.Items.Count + 1);
                    p.Name = Str(d, "name") ?? "?";
                    p.Window = Str(d, "window") ?? "";
                    p.Level = Str(d, "level") ?? "on_pace";
                    p.Verdict = Str(d, "verdict") ?? "";
                    p.Note = Str(d, "note");
                    p.Used = Num(d, "used_percent", 0);
                    p.Elapsed = Num(d, "elapsed_percent", double.NaN);
                    p.HasElapsed = !double.IsNaN(p.Elapsed);
                    if (!p.HasElapsed) p.Elapsed = 0;
                    p.Need = Num(d, "need", 0);
                    p.Free = Bool(d, "free");
                    p.Resets = (long)Num(d, "resets_epoch", 0);
                    p.Released = (long)Num(d, "released_epoch", 0);
                    g.Items.Add(p);
                }
            }
            snap.Groups.Add(g);
        }

        static string Str(Dictionary<string, object> d, string k)
        {
            object v;
            return d.TryGetValue(k, out v) && v != null ? Convert.ToString(v, CultureInfo.InvariantCulture) : null;
        }

        static double Num(Dictionary<string, object> d, string k, double def)
        {
            object v;
            if (!d.TryGetValue(k, out v) || v == null) return def;
            // JSON numbers only (strings such as "NaN" are refused), and only finite ones.
            if (!(v is int || v is long || v is decimal || v is double)) return def;
            double x;
            try { x = Convert.ToDouble(v, CultureInfo.InvariantCulture); }
            catch (Exception) { return def; }
            return double.IsNaN(x) || double.IsInfinity(x) ? def : x;
        }

        static bool Bool(Dictionary<string, object> d, string k)
        {
            object v;
            return d.TryGetValue(k, out v) && v is bool && (bool)v;
        }
    }

    sealed class Settings
    {
        public string Server = "";
        public string Token = "";
        public double PillLeft = double.NaN, PillTop = double.NaN;
        public bool Expanded;

        static string Dir { get { return System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "TokenPace"); } }
        static string FilePath { get { return System.IO.Path.Combine(Dir, "mini.json"); } }
        public bool Configured { get { return Server.Length > 0; } }

        public static Settings Load()
        {
            var s = new Settings();
            try
            {
                if (!File.Exists(FilePath)) return s;
                var d = new JavaScriptSerializer().DeserializeObject(File.ReadAllText(FilePath)) as Dictionary<string, object>;
                if (d == null) return s;
                object v;
                if (d.TryGetValue("server", out v) && v != null) s.Server = Api.Normalize(Convert.ToString(v));
                if (d.TryGetValue("token", out v) && v != null) s.Token = Unprotect(Convert.ToString(v));
                if (d.TryGetValue("left", out v) && v != null) s.PillLeft = Convert.ToDouble(v, CultureInfo.InvariantCulture);
                if (d.TryGetValue("top", out v) && v != null) s.PillTop = Convert.ToDouble(v, CultureInfo.InvariantCulture);
                if (d.TryGetValue("expanded", out v) && v is bool) s.Expanded = (bool)v;
            }
            catch (Exception) { }
            return s;
        }

        public void Save()
        {
            try
            {
                Directory.CreateDirectory(Dir);
                var d = new Dictionary<string, object>();
                d["server"] = Server;
                d["token"] = Protect(Token);   // encrypted for this Windows user (DPAPI)
                if (!double.IsNaN(PillLeft)) d["left"] = Math.Round(PillLeft);
                if (!double.IsNaN(PillTop)) d["top"] = Math.Round(PillTop);
                d["expanded"] = Expanded;
                File.WriteAllText(FilePath, new JavaScriptSerializer().Serialize(d));
            }
            catch (Exception) { }
        }

        static string Protect(string plain)
        {
            if (string.IsNullOrEmpty(plain)) return "";
            return Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(plain), null, DataProtectionScope.CurrentUser));
        }

        static string Unprotect(string stored)
        {
            if (string.IsNullOrEmpty(stored)) return "";
            try { return Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(stored), null, DataProtectionScope.CurrentUser)); }
            catch (Exception) { return ""; }
        }

        const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        public static bool StartsWithWindows
        {
            get
            {
                using (var k = Registry.CurrentUser.OpenSubKey(RunKey)) return k != null && k.GetValue("TokenPaceMini") != null;
            }
            set
            {
                using (var k = Registry.CurrentUser.CreateSubKey(RunKey))
                {
                    if (value) k.SetValue("TokenPaceMini", "\"" + Process.GetCurrentProcess().MainModule.FileName + "\"");
                    else k.DeleteValue("TokenPaceMini", false);
                }
            }
        }
    }

    static class Ui
    {
        public static Brush B(string hex)
        {
            var b = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
            b.Freeze();
            return b;
        }

        public static readonly FontFamily Font = new FontFamily("Segoe UI Variable Text, Segoe UI");
        public static readonly Brush PillBg = B("#1F2023"), PanelBg = B("#232427"), Edge = B("#34363B"),
            Ink = B("#F2F3F5"), Soft = B("#B4B7BD"), Muted = B("#8A8E95"), Track = B("#33363D"),
            Used = B("#7E9BD0"), Link = B("#9DB4E0"), Warn = B("#F5C46B");
        public static readonly Brush SlackHatch = Hatch("#4FB8A6"), AheadHatch = Hatch("#F5A623");

        static Brush Hatch(string hex)
        {
            var c = (Color)ColorConverter.ConvertFromString(hex);
            var tint = Color.FromArgb(80, c.R, c.G, c.B);
            var b = new LinearGradientBrush();
            b.MappingMode = BrushMappingMode.Absolute;
            b.StartPoint = new Point(0, 0);
            b.EndPoint = new Point(3.5, 3.5);
            b.SpreadMethod = GradientSpreadMethod.Repeat;
            b.GradientStops.Add(new GradientStop(c, 0));
            b.GradientStops.Add(new GradientStop(c, 0.42));
            b.GradientStops.Add(new GradientStop(tint, 0.42));
            b.GradientStops.Add(new GradientStop(tint, 1));
            b.Freeze();
            return b;
        }

        // Text colour per level, and the chip's fill and text.
        public static Brush LevelInk(string level)
        {
            switch (level)
            {
                case "use": case "lean_use": return B("#8FD6A0");
                case "save": return B("#F5C46B");
                case "blocked": return B("#FF7A6E");
                case "free": return B("#9DB4E0");
                default: return B("#D6D8DC");
            }
        }

        public static void Chip(string level, out Brush fill, out Brush stroke, out Brush ink)
        {
            stroke = null;
            switch (level)
            {
                case "use": fill = B("#BFE6B0"); ink = B("#18301B"); break;
                case "lean_use": fill = Brushes.Transparent; stroke = B("#5FAE7A"); ink = B("#8FD6A0"); break;
                case "save": fill = B("#F5C46B"); ink = B("#2A1A00"); break;
                case "blocked": fill = B("#F0453A"); ink = Brushes.White; break;
                case "free": fill = B("#34363B"); ink = B("#9DB4E0"); break;
                default: fill = B("#34363B"); ink = B("#D6D8DC"); break;
            }
        }

        public static TextBlock T(string text, double size, Brush fg, FontWeight weight)
        {
            var t = new TextBlock();
            t.Text = text;
            t.FontFamily = Font;
            t.FontSize = size;
            t.Foreground = fg;
            t.FontWeight = weight;
            t.TextTrimming = TextTrimming.CharacterEllipsis;
            return t;
        }

        public static Run R(string text, double size, Brush fg, FontWeight weight)
        {
            var r = new Run(text);
            r.FontSize = size;
            r.Foreground = fg;
            r.FontWeight = weight;
            return r;
        }

        public static string Pace(Plan p)
        {
            if (p.Free) return "free";
            if (p.Level == "blocked") return "used up";
            return p.Need >= 99 ? "99×+" : p.Need.ToString("0.0", CultureInfo.InvariantCulture) + "×";
        }

        public static string Duration(double seconds)
        {
            if (seconds <= 0) return "now";
            var min = (long)Math.Floor(seconds / 60);
            if (min < 1) return "under 1 min";
            long d = min / 1440, h = (min % 1440) / 60, m = min % 60;
            if (d > 0) return d + "d " + h + "h";
            if (h > 0) return h + "h " + m.ToString("00") + "m";
            return m + " min";
        }

        public static double Now() { return (DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds; }

        public static string When(Plan p)
        {
            if (p.Level == "blocked" && p.Released > 0) return "frees in " + Duration(p.Released - Now());
            return p.Resets > 0 ? "resets in " + Duration(p.Resets - Now()) : "reset unknown";
        }

        public static string Pct(double v) { return Math.Round(v).ToString(CultureInfo.InvariantCulture) + "%"; }

        // The Token Pace mark, drawn natively (same geometry as the page's SVG).
        public static FrameworkElement Mark(double size)
        {
            var c = new Canvas();
            c.Width = 32; c.Height = 32;
            Rect(c, 2, 2, 28, 28, 8, "#14263F");
            double[] ys = { 9, 14.4, 19.8 };
            double[] fills = { 8, 14, 4.5 };
            string[] colors = { "#7894C5", "#7894C5", "#4FB8A6" };
            for (int i = 0; i < 3; i++) { Rect(c, 7, ys[i], 18, 3.2, 1.6, "#2E6B62"); Rect(c, 7, ys[i], fills[i], 3.2, 1.6, colors[i]); }
            Rect(c, 18.2, 7, 1.4, 18, 0.7, "#E8E8E8");
            var v = new Viewbox();
            v.Width = size; v.Height = size;
            v.Child = c;
            return v;
        }

        static void Rect(Canvas c, double x, double y, double w, double h, double r, string hex)
        {
            var e = new Rectangle();
            e.Width = w; e.Height = h; e.RadiusX = r; e.RadiusY = r; e.Fill = B(hex);
            Canvas.SetLeft(e, x); Canvas.SetTop(e, y);
            c.Children.Add(e);
        }

        public static Effect Shadow()
        {
            var s = new DropShadowEffect();
            s.BlurRadius = 18; s.ShadowDepth = 3; s.Direction = 270; s.Opacity = 0.45; s.Color = Colors.Black;
            return s;
        }
    }

    // One bar: blue = quota used; hatched teal from there to the time tick = slack,
    // hatched amber past the tick = ahead of pace. Same encoding as the page and the Android widget.
    sealed class PaceBar : FrameworkElement
    {
        readonly double used, elapsed, h;
        readonly bool hasElapsed;

        public PaceBar(double used, double elapsed, bool hasElapsed, double height)
        {
            this.used = Math.Max(0, Math.Min(100, used));
            this.elapsed = Math.Max(0, Math.Min(100, elapsed));
            this.hasElapsed = hasElapsed;
            h = height;
            Height = height + 6;
            HorizontalAlignment = HorizontalAlignment.Stretch;
            SnapsToDevicePixels = true;
        }

        protected override void OnRender(DrawingContext dc)
        {
            double w = ActualWidth;
            if (w <= 0) return;
            double y = 3;
            var track = new RectangleGeometry(new Rect(0, y, w, h), h / 2, h / 2);
            dc.DrawGeometry(Ui.Track, null, track);
            dc.PushClip(track);
            double u = used / 100 * w, e = elapsed / 100 * w;
            dc.DrawRectangle(Ui.Used, null, new Rect(0, y, u, h));
            if (hasElapsed && Math.Abs(e - u) > 0.5) dc.DrawRectangle(e >= u ? Ui.SlackHatch : Ui.AheadHatch, null, new Rect(Math.Min(u, e), y, Math.Abs(e - u), h));
            dc.Pop();
            // Without a time share there is nothing to compare against: the fill alone.
            if (hasElapsed) dc.DrawRectangle(Ui.Ink, null, new Rect(Math.Max(0, Math.Min(w - 2, e - 1)), 0, 2, h + 6));
        }
    }

    sealed class MiniWindow : Window
    {
        const double Width0 = 340, Gap = 8, Pad = 14;
        readonly Settings settings;
        readonly StackPanel stack = new StackPanel();
        Border pill, panel;
        ScrollViewer panelScroll;
        Snapshot data;
        string error;
        bool fetching, above, dragging, renderPending;
        int generation;   // bumped when the server changes, so a late answer from the old one is dropped
        public event Action Changed;

        public Snapshot Data { get { return data; } }
        public string Error { get { return error; } }

        public MiniWindow(Settings settings)
        {
            this.settings = settings;
            Title = "Token Pace";
            WindowStyle = WindowStyle.None;
            AllowsTransparency = true;
            Background = Brushes.Transparent;
            Topmost = true;
            ShowInTaskbar = false;
            ShowActivated = false;
            ResizeMode = ResizeMode.NoResize;
            SizeToContent = SizeToContent.WidthAndHeight;
            stack.Margin = new Thickness(Pad);
            Content = stack;
            SourceInitialized += delegate
            {
                // A tool window stays out of Alt+Tab.
                var hwnd = new WindowInteropHelper(this).Handle;
                SetWindowLong(hwnd, -20, GetWindowLong(hwnd, -20) | 0x80);
            };
            SystemEvents.DisplaySettingsChanged += delegate { Dispatcher.BeginInvoke(new Action(Relayout)); };
            Render();
        }

        [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr hwnd, int index);
        [DllImport("user32.dll")] static extern int SetWindowLong(IntPtr hwnd, int index, int value);

        // Screen coordinates are device pixels; WPF uses 1/96-inch units. This app is system-DPI aware.
        Vector Scale()
        {
            var src = PresentationSource.FromVisual(this);
            if (src != null && src.CompositionTarget != null)
            {
                var m = src.CompositionTarget.TransformToDevice;
                return new Vector(m.M11, m.M22);
            }
            using (var g = Gdi.Graphics.FromHwnd(IntPtr.Zero)) return new Vector(g.DpiX / 96.0, g.DpiY / 96.0);
        }

        Rect ToDips(Gdi.Rectangle r)
        {
            var k = Scale();
            return new Rect(r.Left / k.X, r.Top / k.Y, r.Width / k.X, r.Height / k.Y);
        }

        // Work area of the monitor holding the pill (the nearest one if it is off every screen).
        Rect ScreenArea()
        {
            var k = Scale();
            var pt = new Gdi.Point((int)Math.Round((settings.PillLeft + 30) * k.X), (int)Math.Round((settings.PillTop + 20) * k.Y));
            return ToDips(Forms.Screen.FromPoint(pt).WorkingArea);
        }

        public void PlaceInitially()
        {
            bool onScreen = false;
            if (!double.IsNaN(settings.PillLeft) && !double.IsNaN(settings.PillTop))
            {
                var k = Scale();
                var pt = new Gdi.Point((int)Math.Round((settings.PillLeft + 30) * k.X), (int)Math.Round((settings.PillTop + 20) * k.Y));
                foreach (var s in Forms.Screen.AllScreens) if (s.WorkingArea.Contains(pt)) onScreen = true;
            }
            if (!onScreen)
            {
                // Default: bottom-right of the main screen, just above the taskbar.
                var wa = ToDips(Forms.Screen.PrimaryScreen.WorkingArea);
                settings.PillLeft = wa.Right - Width0 - 12;
                settings.PillTop = wa.Bottom - 70;
            }
            Relayout();
        }

        public void ResetPosition()
        {
            settings.PillLeft = double.NaN;
            settings.PillTop = double.NaN;
            PlaceInitially();
            settings.Save();
            Render();
        }

        public void ServerChanged()
        {
            generation++;
            fetching = false;
            data = null;
            error = null;
            Refresh();
        }

        public void Refresh()
        {
            if (fetching) return;
            if (!settings.Configured) { error = null; Render(); return; }
            fetching = true;
            int gen = generation;
            string server = settings.Server, token = settings.Token;
            ThreadPool.QueueUserWorkItem(delegate
            {
                Snapshot snap = null;
                string err = null;
                try { snap = Api.Fetch(server, token); }
                catch (ApiException e) { err = e.Message; }
                catch (Exception) { err = "Reading failed"; }
                Dispatcher.BeginInvoke(new Action(delegate
                {
                    if (gen != generation) return;   // the server changed meanwhile
                    fetching = false;
                    if (snap != null) { data = snap; error = null; }
                    else error = err;
                    Render();
                }));
            });
        }

        public void SetData(Snapshot snap, string err)
        {
            data = snap;
            error = err;
            Render();
        }

        public void ToggleExpanded()
        {
            settings.Expanded = !settings.Expanded;
            settings.Save();
            Render();
        }

        public void Render()
        {
            if (dragging) { renderPending = true; return; }   // never rebuild the pill under the cursor
            renderPending = false;
            double scrolled = panelScroll != null ? panelScroll.VerticalOffset : 0;
            above = OpensAbove(pill != null ? pill.DesiredSize.Height : 60);   // the arrow needs it before Relayout
            pill = BuildPill();
            panel = settings.Expanded ? BuildPanel() : null;
            Relayout();
            if (panel != null && scrolled > 0) panelScroll.ScrollToVerticalOffset(scrolled);   // keep the place in a long list
            if (Changed != null) Changed();
        }

        bool OpensAbove(double pillH)
        {
            if (double.IsNaN(settings.PillLeft) || double.IsNaN(settings.PillTop)) return false;
            var area = ScreenArea();
            return settings.PillTop + pillH / 2 > area.Top + area.Height / 2;
        }

        // Keeps the pill where the user put it, inside its screen; the panel opens toward the larger
        // free side and scrolls when the plans don't fit.
        void Relayout()
        {
            if (pill == null || dragging) return;
            pill.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
            double pillH = pill.DesiredSize.Height;
            bool placed = !double.IsNaN(settings.PillLeft) && !double.IsNaN(settings.PillTop);
            Rect area = SystemParameters.WorkArea;
            if (placed)
            {
                area = ScreenArea();
                settings.PillLeft = Math.Max(area.Left, Math.Min(area.Right - Width0, settings.PillLeft));
                settings.PillTop = Math.Max(area.Top, Math.Min(area.Bottom - pillH, settings.PillTop));
            }
            above = placed && OpensAbove(pillH);
            stack.Children.Clear();
            if (panel != null && above) { panel.Margin = new Thickness(0, 0, 0, Gap); stack.Children.Add(panel); }
            stack.Children.Add(pill);
            if (panel != null && !above) { panel.Margin = new Thickness(0, Gap, 0, 0); stack.Children.Add(panel); }
            if (!placed) return;
            if (panel != null && panelScroll != null)
            {
                panelScroll.MaxHeight = double.PositiveInfinity;
                panel.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
                double room = above ? settings.PillTop - area.Top : area.Bottom - settings.PillTop - pillH;
                double over = panel.DesiredSize.Height - room;   // DesiredSize includes the gap margin
                if (over > 0)
                {
                    panelScroll.MaxHeight = Math.Max(80, panelScroll.DesiredSize.Height - over);
                    // Measure is cached: without these the panel keeps reporting its old, full height.
                    ((UIElement)panelScroll.Parent).InvalidateMeasure();
                    panel.InvalidateMeasure();
                }
            }
            stack.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
            double panelH = panel != null && above ? panel.DesiredSize.Height : 0;
            Left = settings.PillLeft - Pad;
            Top = settings.PillTop - Pad - panelH;
        }

        Border BuildPill()
        {
            var b = new Border();
            b.Width = Width0;
            b.CornerRadius = new CornerRadius(30);
            b.Background = Ui.PillBg;
            b.BorderBrush = Ui.Edge;
            b.BorderThickness = new Thickness(1);
            b.Padding = new Thickness(12, 9, 10, 9);
            b.Effect = Ui.Shadow();
            b.Cursor = Cursors.SizeAll;

            var g = new Grid();
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var mark = Ui.Mark(38);
            mark.VerticalAlignment = VerticalAlignment.Center;
            g.Children.Add(mark);

            var text = new StackPanel();
            text.Margin = new Thickness(10, 0, 10, 0);
            text.VerticalAlignment = VerticalAlignment.Center;
            Grid.SetColumn(text, 1);
            g.Children.Add(text);

            string chipText;
            string chipLevel;
            Action chipAction = ToggleExpanded;
            var top = data != null ? data.Top() : null;
            if (!settings.Configured)
            {
                text.Children.Add(Ui.T("Token Pace", 15, Ui.Ink, FontWeights.SemiBold));
                text.Children.Add(Ui.T("Connect it to your server", 12.5, Ui.Soft, FontWeights.Normal));
                chipText = "Set up"; chipLevel = "use";
                chipAction = Program.ShowSettings;
            }
            else if (data != null && top == null)
            {
                // Connected, but nothing ranked yet: manual plans not entered, or logins missing.
                text.Children.Add(Ui.T("No readings yet", 15, Ui.Ink, FontWeights.SemiBold));
                text.Children.Add(Ui.T(error != null ? error : "Enter numbers or sign in", 12.5, error != null ? Ui.Warn : Ui.Soft, FontWeights.Normal));
                chipText = "Open page"; chipLevel = "on_pace";
                chipAction = Program.OpenPage;
            }
            else if (top == null)
            {
                text.Children.Add(Ui.T(error != null ? "No reading yet" : "Reading…", 15, Ui.Ink, FontWeights.SemiBold));
                text.Children.Add(Ui.T(error ?? settings.Server, 12.5, error != null ? Ui.Warn : Ui.Soft, FontWeights.Normal));
                chipText = error != null ? "Retry" : "…"; chipLevel = "on_pace";
                chipAction = Refresh;
            }
            else
            {
                var title = Ui.T("", 15, Ui.Ink, FontWeights.SemiBold);
                title.Inlines.Add(Ui.R(top.Name, 15, Ui.Ink, FontWeights.SemiBold));
                title.Inlines.Add(Ui.R("  " + top.Window, 12, Ui.Muted, FontWeights.Normal));
                text.Children.Add(title);
                var sub = Ui.T("", 12.5, Ui.Soft, FontWeights.Normal);
                sub.Inlines.Add(Ui.R(Ui.Pace(top), 12.5, Ui.LevelInk(top.Level), FontWeights.Bold));
                sub.Inlines.Add(Ui.R(" · " + Ui.When(top), 12.5, Ui.Soft, FontWeights.Normal));
                if (error != null) sub.Inlines.Add(Ui.R(" · old reading", 12.5, Ui.Warn, FontWeights.Normal));
                text.Children.Add(sub);
                var bar = new PaceBar(top.Used, top.Elapsed, top.HasElapsed, 5);
                bar.Margin = new Thickness(0, 3, 0, 0);
                text.Children.Add(bar);
                chipText = top.Verdict; chipLevel = top.Level;
            }

            Brush fill, stroke, ink;
            Ui.Chip(chipLevel, out fill, out stroke, out ink);
            var chip = new Border();
            chip.CornerRadius = new CornerRadius(17);
            chip.Background = fill;
            if (stroke != null) { chip.BorderBrush = stroke; chip.BorderThickness = new Thickness(1.2); }
            chip.Padding = new Thickness(13, 6, 13, 7);
            chip.VerticalAlignment = VerticalAlignment.Center;
            chip.Cursor = Cursors.Hand;
            chip.Child = Ui.T(chipText, 13.5, ink, FontWeights.SemiBold);
            chip.MouseLeftButtonDown += delegate(object s, MouseButtonEventArgs e)
            {
                e.Handled = true;
                chipAction();
            };
            Grid.SetColumn(chip, 2);
            g.Children.Add(chip);

            var chev = new Border();
            chev.Width = 26; chev.Height = 26;
            chev.Margin = new Thickness(6, 0, 0, 0);
            chev.Background = Brushes.Transparent;
            chev.Cursor = Cursors.Hand;
            chev.VerticalAlignment = VerticalAlignment.Center;
            bool pointUp = settings.Expanded ? !above : above;
            var path = new System.Windows.Shapes.Path();
            path.Data = Geometry.Parse(pointUp ? "M0,6 L6,0 L12,6" : "M0,0 L6,6 L12,0");
            path.Stroke = Ui.Soft;
            path.StrokeThickness = 1.8;
            path.StrokeStartLineCap = PenLineCap.Round;
            path.StrokeEndLineCap = PenLineCap.Round;
            path.StrokeLineJoin = PenLineJoin.Round;
            path.HorizontalAlignment = HorizontalAlignment.Center;
            path.VerticalAlignment = VerticalAlignment.Center;
            chev.Child = path;
            chev.ToolTip = settings.Expanded ? "Collapse" : "Show all plans";
            chev.MouseLeftButtonDown += delegate(object s, MouseButtonEventArgs e) { e.Handled = true; ToggleExpanded(); };
            Grid.SetColumn(chev, 3);
            g.Children.Add(chev);

            b.Child = g;
            b.MouseLeftButtonDown += delegate(object s, MouseButtonEventArgs e)
            {
                double l0 = Left, t0 = Top;
                dragging = true;
                try { DragMove(); }
                catch (InvalidOperationException) { }
                finally { dragging = false; }
                if (Math.Abs(Left - l0) < 2 && Math.Abs(Top - t0) < 2)
                {
                    if (settings.Configured && data != null && data.Top() != null) ToggleExpanded();
                    else if (renderPending) Render();
                    return;
                }
                double panelH = panel != null && above ? panel.ActualHeight + Gap : 0;
                settings.PillLeft = Left + Pad;
                settings.PillTop = Top + Pad + panelH;
                settings.Save();
                Render();   // the panel may now open on the other side
            };
            b.MouseRightButtonUp += delegate(object s, MouseButtonEventArgs e) { e.Handled = true; Program.ShowMenu(); };
            return b;
        }

        Border BuildPanel()
        {
            var b = new Border();
            b.Width = Width0;
            b.CornerRadius = new CornerRadius(22);
            b.Background = Ui.PanelBg;
            b.BorderBrush = Ui.Edge;
            b.BorderThickness = new Thickness(1);
            b.Padding = new Thickness(16, 10, 16, 12);
            b.Effect = Ui.Shadow();
            var s = new StackPanel();
            b.Child = s;
            var list = new StackPanel();
            panelScroll = new ScrollViewer();
            panelScroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
            panelScroll.Content = list;
            s.Children.Add(panelScroll);

            if (data == null || data.Groups.Count == 0)
            {
                list.Children.Add(Ui.T(error ?? "No reading yet.", 13, Ui.Soft, FontWeights.Normal));
            }
            else
            {
                bool first = true;
                foreach (var g in data.Groups)
                {
                    if (g.Items.Count == 0) continue;
                    var h = Ui.T(g.Label.ToUpperInvariant(), 11, Ui.Muted, FontWeights.SemiBold);
                    h.Margin = new Thickness(0, first ? 2 : 12, 0, 2);
                    list.Children.Add(h);
                    first = false;
                    foreach (var p in g.Items) list.Children.Add(Row(p));
                }
                if (first) list.Children.Add(Ui.T("No plan has a reading yet.", 13, Ui.Soft, FontWeights.Normal));
            }

            var foot = new Grid();
            foot.Margin = new Thickness(0, 10, 0, 0);
            foot.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            foot.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            // Without data the error is already the panel's text; with data it explains why it's old.
            var status = data == null ? "" : error != null ? error : "Updated " + Ago(data.At);
            var st = Ui.T(status, 11.5, error != null ? Ui.Warn : Ui.Muted, FontWeights.Normal);
            st.VerticalAlignment = VerticalAlignment.Center;
            foot.Children.Add(st);
            var links = new StackPanel();
            links.Orientation = Orientation.Horizontal;
            links.Children.Add(Link("Refresh", delegate { Refresh(); }));
            links.Children.Add(Link("Open page", delegate { Program.OpenPage(); }));
            Grid.SetColumn(links, 1);
            foot.Children.Add(links);
            s.Children.Add(foot);
            return b;
        }

        static FrameworkElement Row(Plan p)
        {
            var row = new StackPanel();
            row.Margin = new Thickness(0, 6, 0, 4);
            var top = new Grid();
            top.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            top.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var name = Ui.T("", 13.5, Ui.Ink, FontWeights.SemiBold);
            name.Inlines.Add(Ui.R(p.Rank + ". ", 13, Ui.Muted, FontWeights.Normal));
            name.Inlines.Add(Ui.R(p.Name, 13.5, Ui.Ink, FontWeights.SemiBold));
            name.Inlines.Add(Ui.R("  " + p.Window, 12, Ui.Muted, FontWeights.Normal));
            top.Children.Add(name);
            var pace = Ui.T(Ui.Pace(p), 15, Ui.LevelInk(p.Level), FontWeights.Bold);
            pace.Margin = new Thickness(8, 0, 0, 0);
            Grid.SetColumn(pace, 1);
            top.Children.Add(pace);
            row.Children.Add(top);
            var bar = new PaceBar(p.Used, p.Elapsed, p.HasElapsed, 6);
            bar.Margin = new Thickness(0, 2, 0, 1);
            row.Children.Add(bar);
            var detail = Ui.T("", 11.5, Ui.Muted, FontWeights.Normal);
            detail.Inlines.Add(Ui.R(p.Verdict, 11.5, Ui.LevelInk(p.Level), FontWeights.SemiBold));
            detail.Inlines.Add(Ui.R(" · " + Ui.Pct(p.Used) + " used · " + Ui.When(p), 11.5, Ui.Muted, FontWeights.Normal));
            row.Children.Add(detail);
            return row;
        }

        static FrameworkElement Link(string text, Action click)
        {
            var t = Ui.T(text, 12, Ui.Link, FontWeights.SemiBold);
            t.Margin = new Thickness(14, 0, 0, 0);
            t.Cursor = Cursors.Hand;
            t.MouseLeftButtonDown += delegate(object s, MouseButtonEventArgs e) { e.Handled = true; click(); };
            return t;
        }

        static string Ago(DateTime at)
        {
            var s = (DateTime.UtcNow - at).TotalSeconds;
            return s < 60 ? "just now" : Ui.Duration(s) + " ago";
        }

        // For screenshots and checks: renders the window's content to a PNG without showing it.
        public void RenderTo(string file, string backdrop)
        {
            settings.PillLeft = double.NaN;
            stack.Children.Clear();
            above = false;
            stack.Children.Add(pill);
            if (panel != null) { panel.Margin = new Thickness(0, Gap, 0, 0); stack.Children.Add(panel); }
            var host = new Grid();
            if (backdrop != null)
            {
                var bg = new LinearGradientBrush((Color)ColorConverter.ConvertFromString(backdrop), (Color)ColorConverter.ConvertFromString("#6B7A96"), 45);
                host.Background = bg;
            }
            Content = null;
            host.Children.Add(stack);
            host.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
            host.Arrange(new Rect(host.DesiredSize));
            host.UpdateLayout();
            const double scale = 2;
            var bmp = new RenderTargetBitmap((int)Math.Ceiling(host.ActualWidth * scale), (int)Math.Ceiling(host.ActualHeight * scale), 96 * scale, 96 * scale, PixelFormats.Pbgra32);
            bmp.Render(host);
            var enc = new PngBitmapEncoder();
            enc.Frames.Add(BitmapFrame.Create(bmp));
            using (var f = File.Create(file)) enc.Save(f);
        }
    }

    sealed class SettingsWindow : Window
    {
        public SettingsWindow(Settings settings, Action saved)
        {
            Title = "Token Pace Mini";
            Width = 420;
            SizeToContent = SizeToContent.Height;
            ResizeMode = ResizeMode.NoResize;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            Topmost = true;
            bool closed = false;
            Closed += delegate { closed = true; };
            var s = new StackPanel();
            s.Margin = new Thickness(18);
            Content = s;
            s.Children.Add(Label("Server address"));
            var server = new TextBox();
            server.Text = settings.Configured ? settings.Server : "http://localhost:8787";
            server.Padding = new Thickness(4);
            s.Children.Add(server);
            s.Children.Add(Hint("The address of your Token Pace server, as you open it in a browser."));
            s.Children.Add(Label("Token (if the server has one)"));
            var token = new PasswordBox();
            token.Password = settings.Token;
            token.Padding = new Thickness(4);
            s.Children.Add(token);
            s.Children.Add(Hint("Stored encrypted for your Windows user."));
            var startup = new CheckBox();
            startup.Content = "Start with Windows";
            startup.IsChecked = Settings.StartsWithWindows;
            startup.Margin = new Thickness(0, 12, 0, 0);
            s.Children.Add(startup);
            var msg = new TextBlock();
            msg.TextWrapping = TextWrapping.Wrap;
            msg.Margin = new Thickness(0, 12, 0, 0);
            s.Children.Add(msg);
            var buttons = new StackPanel();
            buttons.Orientation = Orientation.Horizontal;
            buttons.HorizontalAlignment = HorizontalAlignment.Right;
            buttons.Margin = new Thickness(0, 12, 0, 0);
            var save = new Button();
            save.Content = "Test and save";
            save.Padding = new Thickness(14, 5, 14, 5);
            save.IsDefault = true;
            var cancel = new Button();
            cancel.Content = "Cancel";
            cancel.Padding = new Thickness(14, 5, 14, 5);
            cancel.Margin = new Thickness(8, 0, 0, 0);
            cancel.IsCancel = true;
            buttons.Children.Add(save);
            buttons.Children.Add(cancel);
            s.Children.Add(buttons);
            save.Click += delegate
            {
                string url = Api.Normalize(server.Text), tok = token.Password.Trim();
                if (url.Length == 0) { msg.Text = "Enter an http:// or https:// address."; return; }
                save.IsEnabled = false;
                msg.Text = "Checking…";
                bool start = startup.IsChecked == true;
                ThreadPool.QueueUserWorkItem(delegate
                {
                    string err = null;
                    try { Api.Fetch(url, tok); }
                    catch (ApiException e) { err = e.Message; }
                    catch (Exception) { err = "Reading failed"; }
                    Dispatcher.BeginInvoke(new Action(delegate
                    {
                        if (closed) return;   // cancelled while checking: change nothing
                        save.IsEnabled = true;
                        if (err != null) { msg.Text = err + ". Check the address and token."; return; }
                        settings.Server = url;
                        settings.Token = tok;
                        settings.Save();
                        try { Settings.StartsWithWindows = start; } catch (Exception) { }
                        saved();
                        Close();
                    }));
                });
            };
        }

        static TextBlock Label(string t)
        {
            var b = new TextBlock();
            b.Text = t;
            b.FontWeight = FontWeights.SemiBold;
            b.Margin = new Thickness(0, 8, 0, 4);
            return b;
        }

        static TextBlock Hint(string t)
        {
            var b = new TextBlock();
            b.Text = t;
            b.Foreground = Brushes.Gray;
            b.FontSize = 11.5;
            b.TextWrapping = TextWrapping.Wrap;
            b.Margin = new Thickness(0, 3, 0, 0);
            return b;
        }
    }

    // The tray icon: a tiny pace bar for the top plan, with its level as the ring colour.
    sealed class Tray : IDisposable
    {
        readonly Forms.NotifyIcon icon = new Forms.NotifyIcon();
        readonly Forms.ContextMenuStrip menu = new Forms.ContextMenuStrip();
        readonly Forms.ToolStripMenuItem showItem, startItem;
        IntPtr hicon = IntPtr.Zero;

        [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr handle);

        public Tray()
        {
            showItem = new Forms.ToolStripMenuItem("Hide pill", null, delegate { Program.TogglePill(); });
            menu.Items.Add(showItem);
            menu.Items.Add(new Forms.ToolStripMenuItem("Show all plans", null, delegate { Program.ShowExpanded(); }));
            menu.Items.Add(new Forms.ToolStripMenuItem("Reset position", null, delegate { Program.ResetPosition(); }));
            menu.Items.Add(new Forms.ToolStripMenuItem("Refresh now", null, delegate { Program.RefreshNow(); }));
            menu.Items.Add(new Forms.ToolStripMenuItem("Open page", null, delegate { Program.OpenPage(); }));
            menu.Items.Add(new Forms.ToolStripSeparator());
            menu.Items.Add(new Forms.ToolStripMenuItem("Settings…", null, delegate { Program.ShowSettings(); }));
            startItem = new Forms.ToolStripMenuItem("Start with Windows", null, delegate
            {
                try { Settings.StartsWithWindows = !Settings.StartsWithWindows; } catch (Exception) { }
            });
            menu.Items.Add(startItem);
            menu.Items.Add(new Forms.ToolStripSeparator());
            menu.Items.Add(new Forms.ToolStripMenuItem("Quit", null, delegate { Program.Quit(); }));
            menu.Opening += delegate
            {
                showItem.Text = Program.PillVisible ? "Hide pill" : "Show pill";
                startItem.Checked = Settings.StartsWithWindows;
            };
            icon.ContextMenuStrip = menu;
            icon.MouseClick += delegate(object s, Forms.MouseEventArgs e) { if (e.Button == Forms.MouseButtons.Left) Program.TogglePill(); };
            Update(null, null);
            icon.Visible = true;
        }

        public void ShowMenu() { menu.Show(Forms.Cursor.Position); }

        public void Update(Snapshot data, string error)
        {
            var top = data != null ? data.Top() : null;
            string tip = top == null ? "Token Pace · " + (error ?? "no reading yet")
                : top.Name + ": " + top.Verdict + " · " + Ui.Pace(top) + " · " + Ui.When(top);
            icon.Text = tip.Length > 63 ? tip.Substring(0, 62) + "…" : tip;
            var old = hicon;
            using (var bmp = Draw(top))
            {
                hicon = bmp.GetHicon();
                icon.Icon = Gdi.Icon.FromHandle(hicon);
            }
            if (old != IntPtr.Zero) DestroyIcon(old);
        }

        static Gdi.Bitmap Draw(Plan top)
        {
            int s = Math.Max(16, Forms.SystemInformation.SmallIconSize.Width);
            float k = s / 16f;
            var bmp = new Gdi.Bitmap(s, s);
            using (var g = Gdi.Graphics.FromImage(bmp))
            {
                g.SmoothingMode = Gdi.Drawing2D.SmoothingMode.AntiAlias;
                g.Clear(Gdi.Color.Transparent);
                var ring = top == null ? Gdi.Color.FromArgb(0x8A, 0x8E, 0x95) : Gdi.ColorTranslator.FromHtml(RingHex(top.Level));
                using (var p = Rounded(0.5f * k, 0.5f * k, 15 * k, 15 * k, 4 * k))
                using (var fill = new Gdi.SolidBrush(Gdi.Color.FromArgb(0x1F, 0x20, 0x23)))
                using (var pen = new Gdi.Pen(ring, 1.6f * k))
                {
                    g.FillPath(fill, p);
                    g.DrawPath(pen, p);
                }
                float x = 3 * k, w = 10 * k, y = 6.5f * k, h = 3.4f * k;
                using (var track = new Gdi.SolidBrush(Gdi.Color.FromArgb(0x45, 0x48, 0x50))) g.FillRectangle(track, x, y, w, h);
                if (top != null)
                {
                    float u = (float)(Math.Max(0, Math.Min(100, top.Used)) / 100 * w), e = (float)(Math.Max(0, Math.Min(100, top.Elapsed)) / 100 * w);
                    using (var used = new Gdi.SolidBrush(Gdi.Color.FromArgb(0x7E, 0x9B, 0xD0))) g.FillRectangle(used, x, y, u, h);
                    if (top.HasElapsed)
                    {
                        var band = e >= u ? Gdi.Color.FromArgb(0x4F, 0xB8, 0xA6) : Gdi.Color.FromArgb(0xF5, 0xA6, 0x23);
                        using (var bb = new Gdi.SolidBrush(band)) g.FillRectangle(bb, x + Math.Min(u, e), y, Math.Abs(e - u), h);
                        using (var tick = new Gdi.SolidBrush(Gdi.Color.White)) g.FillRectangle(tick, x + e - 0.6f * k, y - 1.6f * k, 1.2f * k, h + 3.2f * k);
                    }
                }
            }
            return bmp;
        }

        static string RingHex(string level)
        {
            switch (level)
            {
                case "use": case "lean_use": return "#8FD6A0";
                case "save": return "#F5C46B";
                case "blocked": return "#FF7A6E";
                default: return "#9DB4E0";
            }
        }

        static Gdi.Drawing2D.GraphicsPath Rounded(float x, float y, float w, float h, float r)
        {
            var p = new Gdi.Drawing2D.GraphicsPath();
            float d = r * 2;
            p.AddArc(x, y, d, d, 180, 90);
            p.AddArc(x + w - d, y, d, d, 270, 90);
            p.AddArc(x + w - d, y + h - d, d, d, 0, 90);
            p.AddArc(x, y + h - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        public void Dispose()
        {
            icon.Visible = false;
            icon.Dispose();
            menu.Dispose();
            if (hicon != IntPtr.Zero) DestroyIcon(hicon);
        }
    }

    static class Program
    {
        public const string Version = "0.3.0";
        static Settings settings;
        static MiniWindow window;
        static Tray tray;
        static SettingsWindow settingsWindow;

        public static bool PillVisible { get { return window != null && window.IsVisible; } }

        [STAThread]
        static int Main(string[] args)
        {
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12 | (SecurityProtocolType)12288;   // TLS 1.3
            string snapshot = Arg(args, "--snapshot");
            if (snapshot != null) return Snapshot(args, snapshot);

            bool created;
            using (var mutex = new Mutex(true, @"Local\TokenPaceMini", out created))
            {
                if (!created) return 0;   // already running: its tray icon is there
                var app = new Application();
                app.ShutdownMode = ShutdownMode.OnExplicitShutdown;
                settings = Settings.Load();
                window = new MiniWindow(settings);
                tray = new Tray();
                window.Changed += delegate { tray.Update(window.Data, window.Error); };
                window.PlaceInitially();
                window.Show();
                window.Refresh();
                var poll = new DispatcherTimer();
                poll.Interval = TimeSpan.FromMinutes(2);
                poll.Tick += delegate { window.Refresh(); };
                poll.Start();
                var tick = new DispatcherTimer();
                tick.Interval = TimeSpan.FromSeconds(30);   // countdowns
                tick.Tick += delegate { window.Render(); };
                tick.Start();
                if (!settings.Configured) ShowSettings();
                app.Run();
                tray.Dispose();
            }
            return 0;
        }

        // tokenpace-mini.exe --snapshot out.png --server http://localhost:8787 [--token T] [--expanded] [--backdrop #hex]
        static int Snapshot(string[] args, string file)
        {
            var app = new Application();
            var s = new Settings();
            s.Server = Api.Normalize(Arg(args, "--server") ?? "http://localhost:8787");
            s.Token = Arg(args, "--token") ?? "";
            s.Expanded = Array.IndexOf(args, "--expanded") >= 0;
            var w = new MiniWindow(s);
            if (s.Server.Length == 0) { w.SetData(null, "Not an http:// or https:// address"); }
            else
            {
                try { w.SetData(Api.Fetch(s.Server, s.Token), null); }
                catch (ApiException e) { w.SetData(null, e.Message); }
                catch (Exception) { w.SetData(null, "Reading failed"); }
            }
            w.RenderTo(file, Arg(args, "--backdrop"));
            return w.Error == null ? 0 : 2;   // so scripts and CI notice a failed read
        }

        static string Arg(string[] args, string name)
        {
            int i = Array.IndexOf(args, name);
            return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
        }

        public static void TogglePill()
        {
            if (window.IsVisible) window.Hide();
            else { window.Show(); window.Render(); }
        }

        public static void ShowExpanded()
        {
            if (!window.IsVisible) window.Show();
            if (!settings.Expanded) window.ToggleExpanded();
        }

        public static void RefreshNow() { window.Refresh(); }

        public static void ResetPosition()
        {
            if (!window.IsVisible) window.Show();
            window.ResetPosition();
        }

        public static void ShowMenu() { tray.ShowMenu(); }

        public static void OpenPage()
        {
            if (!settings.Configured) { ShowSettings(); return; }
            try { Process.Start(settings.Server + "/"); } catch (Exception) { }
        }

        public static void ShowSettings()
        {
            if (settingsWindow != null) { settingsWindow.Activate(); return; }
            settingsWindow = new SettingsWindow(settings, delegate { window.ServerChanged(); });
            settingsWindow.Closed += delegate { settingsWindow = null; };
            settingsWindow.Show();
            settingsWindow.Activate();
        }

        public static void Quit()
        {
            settings.Save();
            Application.Current.Shutdown();
        }
    }
}
