// Offline benchmark adapter for the in-box Windows.Media.Ocr engine.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Reflection;
using System.Linq;
using System.Web.Script.Serialization;
using Windows.Foundation;
using Windows.Globalization;
using Windows.Graphics.Imaging;
using Windows.Media.Ocr;
using Windows.Storage;

public class WindowsOcr
{
    public class InputPage { public string id { get; set; } public string path { get; set; } }

    public static int Main(string[] args)
    {
        try {
            if (args.Length != 3) throw new ArgumentException("Usage: WindowsOcr input.json output.json language-tag");
            Run(args).GetAwaiter().GetResult();
            return 0;
        } catch (Exception e) { Console.Error.WriteLine(e.ToString()); return 1; }
    }

    static Task<T> AsTask<T>(object operation)
    {
        var assembly = Assembly.Load("System.Runtime.WindowsRuntime, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089");
        var extension = assembly.GetType("System.WindowsRuntimeSystemExtensions", true);
        var method = extension.GetMethods().First(m => m.Name == "AsTask" && m.IsGenericMethodDefinition &&
            m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType.Name == "IAsyncOperation`1");
        return (Task<T>)method.MakeGenericMethod(typeof(T)).Invoke(null, new object[] { operation });
    }

    static async Task Run(string[] args)
    {
        var json = new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue };
        var inputs = json.Deserialize<InputPage[]>(File.ReadAllText(args[0]));
        var init = Stopwatch.StartNew();
        var engine = OcrEngine.TryCreateFromLanguage(new Language(args[2]));
        if (engine == null) throw new InvalidOperationException("OCR language unavailable: " + args[2]);
        init.Stop();
        var pages = new List<object>();
        foreach (var item in inputs) {
            var timer = Stopwatch.StartNew();
            var file = await AsTask<StorageFile>(StorageFile.GetFileFromPathAsync(item.path));
            using (var stream = await AsTask<Windows.Storage.Streams.IRandomAccessStreamWithContentType>(file.OpenReadAsync())) {
                var decoder = await AsTask<BitmapDecoder>(BitmapDecoder.CreateAsync(stream));
                using (var bitmap = await AsTask<SoftwareBitmap>(decoder.GetSoftwareBitmapAsync())) {
                    if (bitmap.PixelWidth > OcrEngine.MaxImageDimension || bitmap.PixelHeight > OcrEngine.MaxImageDimension)
                        throw new InvalidOperationException("Page exceeds OCR dimension limit: " + item.id);
                    var decoded = timer.Elapsed.TotalSeconds;
                    var ocr = await AsTask<OcrResult>(engine.RecognizeAsync(bitmap));
                    var inference = timer.Elapsed.TotalSeconds - decoded;
                    var lines = new List<string>();
                    var regions = new List<object>();
                    foreach (var line in ocr.Lines) {
                        lines.Add(line.Text);
                        if (line.Words.Count == 0) continue;
                        double left = Double.PositiveInfinity, right = Double.NegativeInfinity;
                        double top = Double.PositiveInfinity, bottom = Double.NegativeInfinity;
                        foreach (var word in line.Words) {
                            var box = word.BoundingRect;
                            left = Math.Min(left, box.X); right = Math.Max(right, box.X + box.Width);
                            top = Math.Min(top, box.Y); bottom = Math.Max(bottom, box.Y + box.Height);
                        }
                        regions.Add(new { text = line.Text, bbox = new[] { left, top, right, bottom } });
                    }
                    pages.Add(new { id = item.id, decode_s = decoded, infer_s = inference,
                                    text = String.Join("\n", lines), lines = regions });
                }
            }
        }
        File.WriteAllText(args[1], json.Serialize(new { language = args[2], engine_init_s = init.Elapsed.TotalSeconds,
                           max_dimension = OcrEngine.MaxImageDimension, pages = pages }), new UTF8Encoding(false));
    }
}
