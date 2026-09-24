// BridgeSmoke: standalone ABI check for LibASSBridge.dylib.
//
// The app dlopens the bridge exactly the way the patched nPlayer does
// (@executable_path/Frameworks), resolves the 15 exports, verifies the
// dladdr identity, then drives libass through its public boundary and
// reports every step on screen.

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <dlfcn.h>
#import <stdarg.h>
#import <stdio.h>

#define NPA_EXPORT_COUNT 15

static NSString *const kBridgePath = @"@executable_path/Frameworks/LibASSBridge.dylib";
static NSString *const kBridgeBasename = @"LibASSBridge.dylib";

static NSString *const kApiNames[NPA_EXPORT_COUNT] = {
    @"npa_ass_library_init",
    @"npa_ass_library_done",
    @"npa_ass_set_fonts_dir",
    @"npa_ass_set_extract_fonts",
    @"npa_ass_set_message_cb",
    @"npa_ass_renderer_init",
    @"npa_ass_renderer_done",
    @"npa_ass_set_frame_size",
    @"npa_ass_set_fonts",
    @"npa_ass_render_frame",
    @"npa_ass_new_track",
    @"npa_ass_process_codec_private",
    @"npa_ass_process_data",
    @"npa_ass_free_track",
    @"npa_ass_flush_events",
};

typedef struct ass_library ASS_Library;
typedef struct ass_renderer ASS_Renderer;
typedef struct ass_track ASS_Track;
typedef struct ass_image {
    int w;
    int h;
    int stride;
    unsigned char *bitmap;
    uint32_t color;
    int dst_x;
    int dst_y;
    struct ass_image *next;
} ASS_Image;

static NSMutableArray<NSString *> *gLog;
static BOOL gFailed;

static void Report(NSString *format, ...) {
    va_list args;
    va_start(args, format);
    NSString *line = [[NSString alloc] initWithFormat:format arguments:args];
    va_end(args);
    [gLog addObject:line];
    NSLog(@"[BridgeSmoke] %@", line);
}

static void Check(BOOL condition, NSString *description) {
    if (!condition) {
        gFailed = YES;
    }
    Report(@"%@ %@", condition ? @"PASS" : @"FAIL", description);
}

static void SmokeMessageCallback(int level, const char *format, va_list args, void *data) {
    (void)data;
    char buffer[512];
    vsnprintf(buffer, sizeof(buffer), format, args);
    Report(@"libass[%d] %s", level, buffer);
}

static NSString *WriteFontConfig(void) {
    NSString *directory = NSTemporaryDirectory();
    NSString *path = [directory stringByAppendingPathComponent:@"bridgesmoke-fonts.conf"];
    NSString *body =
        @"<?xml version=\"1.0\"?>\n"
        @"<fontconfig>\n"
        @"  <dir>/System/Library/Fonts</dir>\n"
        @"  <dir>/Library/Fonts</dir>\n"
        @"  <cachedir>@CACHE@</cachedir>\n"
        @"</fontconfig>\n";
    body = [body stringByReplacingOccurrencesOfString:@"@CACHE@" withString:directory];
    NSError *error = nil;
    if (![body writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:&error]) {
        Report(@"FAIL fontconfig file: %@", error);
        gFailed = YES;
    }
    setenv("FONTCONFIG_FILE", path.UTF8String, 1);
    setenv("FONTCONFIG_PATH", directory.UTF8String, 1);
    return path;
}

static void RunSmoke(void) {
    Report(@"bridge path %@", kBridgePath);
    void *handle = dlopen(kBridgePath.UTF8String, RTLD_NOW | RTLD_LOCAL);
    if (handle == NULL) {
        Report(@"FAIL dlopen: %s", dlerror());
        gFailed = YES;
        return;
    }
    Report(@"PASS dlopen");

    void *symbols[NPA_EXPORT_COUNT] = {0};
    Dl_info lastInfo = {0};
    for (int index = 0; index < NPA_EXPORT_COUNT; index++) {
        symbols[index] = dlsym(handle, kApiNames[index].UTF8String);
        Check(symbols[index] != NULL, [NSString stringWithFormat:@"dlsym %@", kApiNames[index]]);
        if (symbols[index] == NULL) {
            continue;
        }
        Dl_info info = {0};
        if (dladdr(symbols[index], &info) == 0) {
            Check(NO, [NSString stringWithFormat:@"dladdr %@", kApiNames[index]]);
            continue;
        }
        if (index == 0) {
            lastInfo = info;
        }
        Check(info.dli_fbase == lastInfo.dli_fbase, [NSString stringWithFormat:@"dli_fbase %@", kApiNames[index]]);
        NSString *basename = [[NSString stringWithUTF8String:info.dli_fname ?: ""] lastPathComponent];
        Check([basename isEqualToString:kBridgeBasename], [NSString stringWithFormat:@"dli_fname %@ -> %@", kApiNames[index], basename]);
    }

    typedef ASS_Library *(*LibraryInit)(void);
    typedef void (*LibraryDone)(ASS_Library *);
    typedef void (*SetFontsDir)(ASS_Library *, const char *);
    typedef void (*SetExtractFonts)(ASS_Library *, int);
    typedef void (*SetMessageCb)(ASS_Library *, void (*)(int, const char *, va_list, void *), void *);
    typedef ASS_Renderer *(*RendererInit)(ASS_Library *);
    typedef void (*RendererDone)(ASS_Renderer *);
    typedef void (*SetFrameSize)(ASS_Renderer *, int, int);
    typedef void (*SetFonts)(ASS_Renderer *, const char *, const char *, int, const char *, int);
    typedef ASS_Image *(*RenderFrame)(ASS_Renderer *, ASS_Track *, long long, int *);
    typedef ASS_Track *(*NewTrack)(ASS_Library *);
    typedef void (*ProcessCodecPrivate)(ASS_Track *, const char *, int);
    typedef void (*ProcessData)(ASS_Track *, const char *, int);
    typedef void (*FreeTrack)(ASS_Track *);
    typedef void (*FlushEvents)(ASS_Track *);

    LibraryInit libraryInit = (LibraryInit)symbols[0];
    LibraryDone libraryDone = (LibraryDone)symbols[1];
    SetFontsDir setFontsDir = (SetFontsDir)symbols[2];
    SetExtractFonts setExtractFonts = (SetExtractFonts)symbols[3];
    SetMessageCb setMessageCb = (SetMessageCb)symbols[4];
    RendererInit rendererInit = (RendererInit)symbols[5];
    RendererDone rendererDone = (RendererDone)symbols[6];
    SetFrameSize setFrameSize = (SetFrameSize)symbols[7];
    SetFonts setFonts = (SetFonts)symbols[8];
    RenderFrame renderFrame = (RenderFrame)symbols[9];
    NewTrack newTrack = (NewTrack)symbols[10];
    ProcessCodecPrivate processCodecPrivate = (ProcessCodecPrivate)symbols[11];
    ProcessData processData = (ProcessData)symbols[12];
    FreeTrack freeTrack = (FreeTrack)symbols[13];
    FlushEvents flushEvents = (FlushEvents)symbols[14];

    WriteFontConfig();

    ASS_Library *library = libraryInit();
    Check(library != NULL, @"library_init");
    if (library == NULL) {
        return;
    }
    setFontsDir(library, "/System/Library/Fonts");
    setExtractFonts(library, 1);
    setMessageCb(library, SmokeMessageCallback, NULL);
    Report(@"PASS library configuration");

    ASS_Renderer *renderer = rendererInit(library);
    Check(renderer != NULL, @"renderer_init");
    if (renderer == NULL) {
        libraryDone(library);
        return;
    }
    setFrameSize(renderer, 640, 360);
    setFonts(renderer, NULL, NULL, 3 /* ASS_FONTPROVIDER_FONTCONFIG */, NULL, 1);
    Report(@"PASS frame size and fonts");

    ASS_Track *track = newTrack(library);
    Check(track != NULL, @"new_track");
    if (track == NULL) {
        rendererDone(renderer);
        libraryDone(library);
        return;
    }
    const char *header =
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 640\n"
        "PlayResY: 360\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Helvetica,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n"
        "\n"
        "[Events]\n";
    processCodecPrivate(track, header, (int)strlen(header));
    const char *event =
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:10.00,Default,,0,0,0,,Smoke\n";
    processData(track, event, (int)strlen(event));
    flushEvents(track);
    Report(@"PASS track setup");

    int detect_change = 0;
    ASS_Image *image = renderFrame(renderer, track, 1000, &detect_change);
    Check(image != NULL, @"render_frame produced an image");
    int images = 0;
    for (ASS_Image *current = image; current != NULL && images < 64; current = current->next) {
        images++;
        Check(current->w > 0 && current->h > 0, @"image has a positive size");
        Check(current->bitmap != NULL, @"image has a bitmap");
        Check(current->stride >= current->w, @"image stride covers its width");
        Check(current->dst_x >= 0 && current->dst_y >= 0, @"image destination is inside the frame");
    }
    Check(images > 0, @"image list is not empty");
    Report(@"images: %d detect_change: %d", images, detect_change);

    // App-compatible teardown: release the renderer and library first.
    rendererDone(renderer);
    libraryDone(library);
    freeTrack(track);
    Report(@"PASS app-compatible teardown");

    dlclose(handle);
    Report(@"PASS dlclose");
}

@interface SmokeViewController : UIViewController
@property(nonatomic, strong) UITextView *textView;
@end

@implementation SmokeViewController

- (void)viewDidLoad {
    [super viewDidLoad];
    gLog = [NSMutableArray array];
    gFailed = NO;
    RunSmoke();
    Report(@"SMOKE: %@", gFailed ? @"FAIL" : @"PASS");

    self.view.backgroundColor = UIColor.systemBackgroundColor;
    self.textView = [[UITextView alloc] initWithFrame:self.view.bounds];
    self.textView.editable = NO;
    self.textView.text = [gLog componentsJoinedByString:@"\n"];
    self.textView.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:self.textView];
}

@end

@interface SmokeAppDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@end

@implementation SmokeAppDelegate

- (BOOL)application:(UIApplication *)application
    didFinishLaunchingWithOptions:(NSDictionary *)options {
    (void)application;
    (void)options;
    self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    self.window.rootViewController = [[SmokeViewController alloc] init];
    [self.window makeKeyAndVisible];
    return YES;
}

@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass(SmokeAppDelegate.class));
    }
}
