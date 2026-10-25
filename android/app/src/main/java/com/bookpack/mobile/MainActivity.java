package com.bookpack.mobile;

import android.app.*;
import android.content.*;
import android.database.Cursor;
import android.graphics.Color;
import android.net.Uri;
import android.os.*;
import android.provider.OpenableColumns;
import android.view.*;
import android.webkit.*;
import android.widget.*;
import com.chaquo.python.Python;
import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.util.concurrent.Future;

public class MainActivity extends Activity {
    private WebView web;
    private String savePath, saveName;
    private ValueCallback<Uri[]> fileCallback;
    private android.database.ContentObserver motionObserver;
    private static final int SAVE = 101, CHOOSE = 102, IMPORT = 103;
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        if (Build.VERSION.SDK_INT >= 30) getWindow().setDecorFitsSystemWindows(false);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | (Build.VERSION.SDK_INT >= 26 ? View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR : 0));
        getWindow().setStatusBarColor(Color.TRANSPARENT); getWindow().setNavigationBarColor(Color.TRANSPARENT);
        FrameLayout root = new FrameLayout(this); root.setBackgroundColor(Color.rgb(212,229,237));
        web = new WebView(this); web.setBackgroundColor(Color.rgb(212,229,237));
        root.addView(web, new FrameLayout.LayoutParams(-1,-1)); setContentView(root);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                android.graphics.Insets ime = insets.getInsets(WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom,ime.bottom));
                return WindowInsets.CONSUMED;
            }
            view.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
            return insets.replaceSystemWindowInsets(0,0,0,0);
        });
        web.getSettings().setJavaScriptEnabled(true); web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setAllowFileAccess(false); web.getSettings().setAllowContentAccess(true);
        web.getSettings().setTextZoom(100); web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        web.addJavascriptInterface(new NativeHost(), "AndroidHost");
        RadarUpdateJob.schedule(this,getSharedPreferences("bookpack",MODE_PRIVATE).getBoolean("dailyRadar",true));
        motionObserver=new android.database.ContentObserver(new Handler(Looper.getMainLooper())) {
            @Override public void onChange(boolean selfChange){publishEnvironment();}
        };
        getContentResolver().registerContentObserver(android.provider.Settings.Global.getUriFor(android.provider.Settings.Global.ANIMATOR_DURATION_SCALE),false,motionObserver);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if ("127.0.0.1".equals(uri.getHost())) return false;
                if ("https".equals(uri.getScheme()) || "http".equals(uri.getScheme())) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); } catch (ActivityNotFoundException error) { message("没有可打开链接的应用。"); }
                }
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null); fileCallback = callback;
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                startActivityForResult(intent, CHOOSE); return true;
            }
        });
        web.setDownloadListener((url, agent, disposition, mime, length) -> {
            String name = URLUtil.guessFileName(url, disposition, mime);
            new NativeHost().save(Uri.parse(url).getEncodedPath(), name);
        });
        if (saved != null) { savePath=saved.getString("savePath"); saveName=saved.getString("saveName"); }
        if (Build.VERSION.SDK_INT >= 33) getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::handleBack);
        web.loadDataWithBaseURL(null, "<html lang='zh-CN'><meta name='viewport' content='width=device-width'><body style='background:#d4e5ed;color:#123c51;font:16px sans-serif;display:grid;place-items:center;height:90vh'>正在启动书包…</body></html>", "text/html", "UTF-8", null);
        Future<String> ready = CoreRuntime.start(this);
        String hash = saved == null ? "" : saved.getString("hash", "");
        CoreRuntime.IO.execute(() -> { try { String url=ready.get(); runOnUiThread(() -> web.loadUrl(url+hash)); } catch (Exception error) { android.util.Log.e("Bookpack","Startup",error); runOnUiThread(() -> web.loadData("<meta charset='utf-8'><p>书包启动失败，请重新打开应用。</p>","text/html","UTF-8")); } });
    }
    private void message(String text) { runOnUiThread(() -> { Toast.makeText(this,text,Toast.LENGTH_LONG).show(); if(web!=null)web.evaluateJavascript("window.nativeFileResult && window.nativeFileResult("+JSONObject.quote(text)+")",null); }); }
    private void legadoResult(JSONObject result) {
        if (web != null) web.evaluateJavascript("window.onLegadoResult && window.onLegadoResult(" + result + ")", null);
    }
    private void handleBack() { web.evaluateJavascript("typeof window.bookpackBack==='function' && window.bookpackBack()", value -> { if (!"true".equals(value)) finish(); }); }
    private String environment() {
        return "{\"reducedMotion\":"+(android.provider.Settings.Global.getFloat(getContentResolver(),android.provider.Settings.Global.ANIMATOR_DURATION_SCALE,1f)==0f)+",\"dailyRadar\":"+getSharedPreferences("bookpack",MODE_PRIVATE).getBoolean("dailyRadar",true)+",\"version\":\"0.3.0\"}";
    }
    private void publishEnvironment(){if(web!=null)web.evaluateJavascript("window.onNativeEnvironmentChanged && window.onNativeEnvironmentChanged("+environment()+")",null);}
    @Override protected void onResume(){super.onResume();if(web!=null)publishEnvironment();}
    @Override protected void onDestroy(){if(motionObserver!=null)getContentResolver().unregisterContentObserver(motionObserver);super.onDestroy();}
    @Override public void onBackPressed() { handleBack(); }
    @Override protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);out.putString("savePath",savePath);out.putString("saveName",saveName);
        String url=web.getUrl();out.putString("hash",url!=null&&url.contains("#")?url.substring(url.indexOf('#')):"");
    }
    final class NativeHost {
        @JavascriptInterface public String getLegadoInfo() { return LegadoBridge.status(MainActivity.this).toString(); }
        @JavascriptInterface public void openLegado(String identity) {
            CoreRuntime.IO.execute(() -> {
                try {
                    JSONObject book = BookFileProvider.source(MainActivity.this, identity);
                    LegadoBridge.Handoff handoff = LegadoBridge.prepare(MainActivity.this, book);
                    runOnUiThread(() -> {
                        try { legadoResult(LegadoBridge.launch(MainActivity.this, handoff)); }
                        catch (Exception error) { legadoFailure(error, identity); }
                    });
                } catch (Exception error) { runOnUiThread(() -> legadoFailure(error, identity)); }
            });
        }
        private void legadoFailure(Exception error, String identity) {
            try { legadoResult(new JSONObject().put("ok", false).put("id", identity).put("reason", "open_failed")
                    .put("message", "未能用 Legado 打开，请检查安装状态及书籍文件后重试。")); }
            catch (org.json.JSONException ignored) { }
            android.util.Log.w("Bookpack", "Legado file handoff", error);
        }
        @JavascriptInterface public String getEnvironment(){return environment();}
        @JavascriptInterface public void setDailyRadar(boolean enabled){
            getSharedPreferences("bookpack",MODE_PRIVATE).edit().putBoolean("dailyRadar",enabled).apply();RadarUpdateJob.schedule(MainActivity.this,enabled);
            CoreRuntime.IO.execute(()->{try{CoreRuntime.start(MainActivity.this).get();Python.getInstance().getModule("android_runtime").callAttr("configure_daily",enabled);}catch(Exception ignored){}});
        }
        @JavascriptInterface public void setTheme(boolean dark){runOnUiThread(()->{
            int color=dark?Color.rgb(10,16,26):Color.rgb(212,229,237);
            web.setBackgroundColor(color);((View)web.getParent()).setBackgroundColor(color);
            int flags=View.SYSTEM_UI_FLAG_LAYOUT_STABLE|View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN;
            if(!dark)flags|=View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR|(Build.VERSION.SDK_INT>=26?View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR:0);
            getWindow().getDecorView().setSystemUiVisibility(flags);
        });}
        @JavascriptInterface public String getPreferences() { return getSharedPreferences("bookpack",MODE_PRIVATE).getString("appearance","{}"); }
        @JavascriptInterface public void savePreferences(String json) { getSharedPreferences("bookpack",MODE_PRIVATE).edit().putString("appearance",json).apply(); }
        @JavascriptInterface public void prepareTask() { runOnUiThread(() -> TaskService.keep(MainActivity.this,"正在准备获取…")); }
        @JavascriptInterface public void save(String path, String name) {
            if(path==null||!path.startsWith("/api/archive/"))return;
            runOnUiThread(() -> {
                savePath=path;saveName=name;
                Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/zip").putExtra(Intent.EXTRA_TITLE,name);
                startActivityForResult(intent,SAVE);
            });
        }
        @JavascriptInterface public void share(String path, String name) {
            if(path==null||!path.startsWith("/api/archive/"))return;
            CoreRuntime.exports.incrementAndGet();runOnUiThread(() -> TaskService.keep(MainActivity.this,"正在准备分享…"));
            CoreRuntime.IO.execute(() -> {
                try {
                    File directory=new File(getCacheDir(),"shared");if(!directory.isDirectory()&&!directory.mkdirs())throw new IOException("Cannot create shared folder");
                    String safe=name.replaceAll("[\\\\/:*?\"<>|]","_");File file=new File(directory,safe);
                    try(OutputStream out=new FileOutputStream(file)){download(path,out);}
                    Uri uri=new Uri.Builder().scheme("content").authority("com.bookpack.mobile.files").appendPath(file.getName()).build();
                    Intent intent=new Intent(Intent.ACTION_SEND).setType("application/zip").putExtra(Intent.EXTRA_STREAM,uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    runOnUiThread(() -> startActivity(Intent.createChooser(intent,"分享书包")));
                } catch(Exception error){message("分享准备失败："+error.getMessage());}
                finally{CoreRuntime.exports.decrementAndGet();}
            });
        }
        @JavascriptInterface public void importBook() { runOnUiThread(() -> startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*").putExtra(Intent.EXTRA_MIME_TYPES,new String[]{"text/plain","application/epub+zip"}),IMPORT)); }
    }
    private void download(String path,OutputStream output) throws Exception {
        String base=CoreRuntime.start(this).get();
        HttpURLConnection connection=(HttpURLConnection)new URL(base.substring(0,base.length()-1)+path).openConnection();
        connection.setConnectTimeout(15000);connection.setReadTimeout(30000);
        try{if(connection.getResponseCode()!=200)throw new IOException("文件暂不可用，请重新打包");try(InputStream input=connection.getInputStream()){CoreRuntime.transfer(input,output);}}finally{connection.disconnect();}
    }
    @Override protected void onActivityResult(int request,int result,Intent data) {
        super.onActivityResult(request,result,data);
        if(request==CHOOSE){if(fileCallback!=null){fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result,data));fileCallback=null;}return;}
        if(result!=RESULT_OK||data==null||data.getData()==null)return;
        Uri uri=data.getData();
        if(request==SAVE&&savePath!=null){
            String path=savePath;savePath=null;CoreRuntime.exports.incrementAndGet();TaskService.keep(this,"正在保存书包…");
            CoreRuntime.IO.execute(() -> {try(OutputStream output=getContentResolver().openOutputStream(uri,"w")){if(output==null)throw new IOException("无法写入所选位置");download(path,output);message("书包已保存到所选位置。");}catch(Exception error){message("保存失败："+error.getMessage());}finally{CoreRuntime.exports.decrementAndGet();}});
        } else if(request==IMPORT){
            CoreRuntime.IO.execute(() -> {try{
                String name="导入书籍.txt";try(Cursor cursor=getContentResolver().query(uri,new String[]{OpenableColumns.DISPLAY_NAME},null,null,null)){if(cursor!=null&&cursor.moveToFirst())name=cursor.getString(0);}
                if(!name.toLowerCase().endsWith(".txt")&&!name.toLowerCase().endsWith(".epub"))throw new IOException("请选择 TXT 或 EPUB 文件");
                File directory=new File(CoreRuntime.home(this),"本地书库");if(!directory.isDirectory()&&!directory.mkdirs())throw new IOException("无法创建书库");
                File target=new File(directory,name.replaceAll("[\\\\/:*?\"<>|]","_"));
                try(InputStream in=getContentResolver().openInputStream(uri);OutputStream out=new FileOutputStream(target)){if(in==null)throw new IOException("无法读取文件");CoreRuntime.transfer(in,out);}
                Python.getInstance().getModule("android_runtime").callAttr("import_directory",directory.getAbsolutePath());message("已导入，可以按书名搜索。");
            }catch(Exception error){message("导入失败："+error.getMessage());}});
        }
    }
}
