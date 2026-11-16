package app.marginalia.mobile;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.ComponentActivity;
import androidx.annotation.NonNull;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.Preview;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.camera.view.PreviewView;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.core.view.WindowCompat;

import com.google.common.util.concurrent.ListenableFuture;
import com.google.mlkit.vision.barcode.BarcodeScanning;
import com.google.mlkit.vision.barcode.common.Barcode;
import com.google.mlkit.vision.barcode.BarcodeScanner;
import com.google.mlkit.vision.common.InputImage;

import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends ComponentActivity {
    private static final int CAMERA_REQUEST = 42;
    private static final String PREFS = "marginalia_mobile";
    private static final String SERVER_KEY = "server_url";
    private SharedPreferences prefs;
    private WebView webView;
    private ExecutorService cameraExecutor;
    private BarcodeScanner barcodeScanner;
    private boolean scanning;
    private String currentServer = "";

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        WindowCompat.setDecorFitsSystemWindows(getWindow(), true);
        getWindow().setStatusBarColor(Color.WHITE);
        getWindow().setNavigationBarColor(Color.WHITE);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String saved = prefs.getString(SERVER_KEY, "");
        if (saved == null || saved.trim().isEmpty()) showConnectPage("");
        else loadReader(saved);
    }

    private int dp(float value) { return (int) (value * getResources().getDisplayMetrics().density + 0.5f); }
    private TextView text(String value, float size, int color) {
        TextView t = new TextView(this); t.setText(value); t.setTextSize(size); t.setTextColor(color); return t;
    }
    private LinearLayout column() { LinearLayout l = new LinearLayout(this); l.setOrientation(LinearLayout.VERTICAL); l.setPadding(dp(24), dp(24), dp(24), dp(24)); return l; }
    private Button button(String label) { Button b = new Button(this); b.setText(label); b.setTextColor(Color.WHITE); b.setTextSize(14); b.setAllCaps(false); b.setBackgroundColor(Color.rgb(17,17,17)); return b; }

    private void showConnectPage(String message) {
        LinearLayout root = column(); root.setBackgroundColor(Color.WHITE); root.setGravity(Gravity.CENTER_HORIZONTAL);
        TextView brand = text("Marginalia", 30, Color.BLACK); brand.setTypeface(null, android.graphics.Typeface.BOLD);
        root.addView(brand, new LinearLayout.LayoutParams(-1, -2));
        TextView title = text("连接电脑端", 25, Color.BLACK); title.setTypeface(null, android.graphics.Typeface.BOLD); title.setPadding(0, dp(58), 0, dp(8)); root.addView(title);
        TextView desc = text("手机和电脑连接同一个 Wi‑Fi。输入电脑端地址，或扫描电脑设置页显示的二维码。", 15, Color.DKGRAY); desc.setLineSpacing(0, 1.25f); root.addView(desc);
        EditText address = new EditText(this); address.setSingleLine(true); address.setHint("例如 192.168.1.20:8765"); address.setText(message); address.setTextSize(16); address.setPadding(dp(12), 0, dp(12), 0);
        LinearLayout.LayoutParams inputParams = new LinearLayout.LayoutParams(-1, dp(54)); inputParams.topMargin = dp(26); root.addView(address, inputParams);
        Button connect = button("连接"); LinearLayout.LayoutParams connectParams = new LinearLayout.LayoutParams(-1, dp(50)); connectParams.topMargin = dp(12); root.addView(connect, connectParams);
        Button scan = new Button(this); scan.setText("扫描电脑二维码"); scan.setTextSize(14); scan.setAllCaps(false); scan.setBackgroundColor(Color.TRANSPARENT); scan.setTextColor(Color.BLACK); root.addView(scan, new LinearLayout.LayoutParams(-1, dp(48)));
        TextView hint = text("连接后，论文阅读、翻译、公式、参考文献、笔记和任务都在电脑端实时处理。", 13, Color.GRAY); hint.setPadding(0, dp(22), 0, 0); root.addView(hint);
        if (!message.isEmpty()) { TextView error = text(message, 13, Color.rgb(160, 30, 30)); error.setPadding(0, dp(12), 0, 0); root.addView(error); }
        connect.setOnClickListener(v -> connect(address.getText().toString()));
        scan.setOnClickListener(v -> requestScanner());
        setContentView(root);
    }

    private void connect(String raw) {
        String url = normalize(raw);
        if (url == null) { showConnectPage("地址格式不正确，请输入电脑 IP 和端口。 "); return; }
        prefs.edit().putString(SERVER_KEY, url).apply(); loadReader(url);
    }

    private String normalize(String raw) {
        String value = raw == null ? "" : raw.trim(); if (value.isEmpty()) return null;
        if (!value.matches("^[a-zA-Z][a-zA-Z0-9+.-]*://.*$")) value = "http://" + value;
        Uri uri = Uri.parse(value); String scheme = uri.getScheme();
        if (!("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme)) || uri.getHost() == null) return null;
        return value.replaceAll("/+$", "");
    }

    private void loadReader(String server) {
        currentServer = server;
        LinearLayout root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(Color.WHITE); root.setFitsSystemWindows(true);
        LinearLayout bar = new LinearLayout(this); bar.setGravity(Gravity.CENTER_VERTICAL); bar.setPadding(dp(16), 0, dp(8), 0);
        TextView title = text("Marginalia", 19, Color.BLACK); title.setTypeface(null, android.graphics.Typeface.BOLD); bar.addView(title, new LinearLayout.LayoutParams(0, dp(50), 1));
        Button change = new Button(this); change.setText("切换电脑"); change.setTextSize(12); change.setAllCaps(false); change.setBackgroundColor(Color.TRANSPARENT); change.setTextColor(Color.DKGRAY); bar.addView(change, new LinearLayout.LayoutParams(-2, dp(50)));
        root.addView(bar); change.setOnClickListener(v -> showConnectPage(server));
        FrameLayout content = new FrameLayout(this); root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));
        webView = new WebView(this); webView.setBackgroundColor(Color.WHITE); WebSettings settings = webView.getSettings(); settings.setJavaScriptEnabled(true); settings.setDomStorageEnabled(true); settings.setDatabaseEnabled(true); settings.setBuiltInZoomControls(false); settings.setDisplayZoomControls(false); settings.setUseWideViewPort(true); settings.setLoadWithOverviewMode(false); settings.setUserAgentString(settings.getUserAgentString() + " MarginaliaMobile/0.1");
        content.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        LinearLayout state = new LinearLayout(this); state.setOrientation(LinearLayout.VERTICAL); state.setGravity(Gravity.CENTER_HORIZONTAL); state.setPadding(dp(30), dp(24), dp(30), dp(24)); state.setBackgroundColor(Color.WHITE);
        TextView stateTitle = text("正在连接电脑端…", 18, Color.BLACK); stateTitle.setGravity(Gravity.CENTER); state.addView(stateTitle, new LinearLayout.LayoutParams(-1, -2));
        TextView stateDetail = text(server, 13, Color.GRAY); stateDetail.setGravity(Gravity.CENTER); stateDetail.setPadding(0, dp(10), 0, dp(18)); state.addView(stateDetail, new LinearLayout.LayoutParams(-1, -2));
        Button retry = button("重新连接"); retry.setVisibility(View.GONE); state.addView(retry, new LinearLayout.LayoutParams(-1, dp(48))); Button changeButton = new Button(this); changeButton.setText("更换电脑"); changeButton.setTextSize(14); changeButton.setAllCaps(false); changeButton.setBackgroundColor(Color.TRANSPARENT); changeButton.setTextColor(Color.DKGRAY); changeButton.setVisibility(View.GONE); state.addView(changeButton, new LinearLayout.LayoutParams(-1, dp(46))); content.addView(state, new FrameLayout.LayoutParams(-1, -1));
        retry.setOnClickListener(v -> { stateTitle.setText("正在连接电脑端…"); stateDetail.setText(server); retry.setVisibility(View.GONE); changeButton.setVisibility(View.GONE); webView.setVisibility(View.INVISIBLE); webView.reload(); }); changeButton.setOnClickListener(v -> showConnectPage(server));
        webView.setWebChromeClient(new WebChromeClient()); webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) { return false; }
            @Override public void onPageFinished(WebView view, String url) { webView.setVisibility(View.VISIBLE); state.setVisibility(View.GONE); }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, android.webkit.WebResourceError error) { if (!request.isForMainFrame()) return; webView.setVisibility(View.INVISIBLE); state.setVisibility(View.VISIBLE); stateTitle.setText("电脑端暂时无法访问"); stateDetail.setText("请确认电脑端已启动，并且手机和电脑连接同一个 Wi‑Fi。\n" + currentServer); retry.setVisibility(View.VISIBLE); changeButton.setVisibility(View.VISIBLE); }
        });
        webView.setVisibility(View.INVISIBLE); setContentView(root); webView.loadUrl(server + "/?mobile=1#/library");
    }

    private void requestScanner() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) { ActivityCompat.requestPermissions(this, new String[]{Manifest.permission.CAMERA}, CAMERA_REQUEST); return; }
        showScanner();
    }
    @Override public void onRequestPermissionsResult(int request, @NonNull String[] permissions, @NonNull int[] results) { super.onRequestPermissionsResult(request, permissions, results); if (request == CAMERA_REQUEST && results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) showScanner(); else Toast.makeText(this, "需要相机权限才能扫描二维码", Toast.LENGTH_LONG).show(); }

    private void showScanner() {
        scanning = true; cameraExecutor = Executors.newSingleThreadExecutor(); barcodeScanner = BarcodeScanning.getClient();
        FrameLayout root = new FrameLayout(this); PreviewView preview = new PreviewView(this); root.addView(preview, new FrameLayout.LayoutParams(-1, -1));
        TextView guide = text("将电脑端二维码放入框内", 16, Color.WHITE); guide.setGravity(Gravity.CENTER); guide.setBackgroundColor(0x66000000); FrameLayout.LayoutParams gp = new FrameLayout.LayoutParams(-1, dp(58), Gravity.TOP); gp.topMargin = dp(38); root.addView(guide, gp);
        Button close = button("取消"); FrameLayout.LayoutParams cp = new FrameLayout.LayoutParams(dp(110), dp(48), Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL); cp.bottomMargin = dp(36); root.addView(close, cp); close.setOnClickListener(v -> stopScanner()); setContentView(root);
        ListenableFuture<ProcessCameraProvider> future = ProcessCameraProvider.getInstance(this); future.addListener(() -> { try { ProcessCameraProvider provider = future.get(); Preview cameraPreview = new Preview.Builder().build(); cameraPreview.setSurfaceProvider(preview.getSurfaceProvider()); ImageAnalysis analysis = new ImageAnalysis.Builder().setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build(); analysis.setAnalyzer(cameraExecutor, this::analyze); provider.unbindAll(); provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, cameraPreview, analysis); } catch (Exception ignored) { Toast.makeText(this, "无法启动相机", Toast.LENGTH_LONG).show(); stopScanner(); } }, ContextCompat.getMainExecutor(this));
    }
    private void analyze(ImageProxy proxy) { if (!scanning || proxy.getImage() == null) { proxy.close(); return; } InputImage image = InputImage.fromMediaImage(proxy.getImage(), proxy.getImageInfo().getRotationDegrees()); barcodeScanner.process(image).addOnSuccessListener(codes -> { for (Barcode code : codes) { String raw = code.getRawValue(); if (raw != null && normalize(raw) != null) { runOnUiThread(() -> { String url = normalize(raw); stopScanner(); connect(url); }); break; } } }).addOnCompleteListener(done -> proxy.close()); }
    private void stopScanner() { scanning = false; if (cameraExecutor != null) { cameraExecutor.shutdownNow(); cameraExecutor = null; } if (barcodeScanner != null) { barcodeScanner.close(); barcodeScanner = null; } String saved = prefs.getString(SERVER_KEY, ""); if (saved == null || saved.isEmpty()) showConnectPage(""); else loadReader(saved); }

    @Override public void onBackPressed() { if (scanning) { stopScanner(); return; } if (webView != null && webView.canGoBack()) webView.goBack(); else super.onBackPressed(); }
    @Override protected void onDestroy() { scanning = false; if (cameraExecutor != null) cameraExecutor.shutdownNow(); if (barcodeScanner != null) barcodeScanner.close(); if (webView != null) webView.destroy(); super.onDestroy(); }
}
