package local.chaoxing.notify;
import android.app.*;
import android.os.*;
import android.content.*;
import android.provider.Settings;
import android.widget.*;
import java.io.*;
import org.json.*;

public class MainActivity extends Activity {
  TextView status;
  protected void onCreate(Bundle saved) {
    super.onCreate(saved);
    LinearLayout box=new LinearLayout(this);box.setOrientation(1);box.setPadding(36,48,36,24);
    TextView title=new TextView(this);title.setText("学习通课程通知桥");title.setTextSize(24);box.addView(title);
    TextView info=new TextView(this);info.setText("只接收学习通通知并发送到这台电脑。不会读取其他应用通知，不会刷新学习通，也不会自行答题。\n请先完成本机配对，再开启通知使用权。");box.addView(info);
    status=new TextView(this);box.addView(status);
    Button permission=new Button(this);permission.setText("打开通知使用权设置");permission.setOnClickListener(v->startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)));box.addView(permission);
    Button test=new Button(this);test.setText("发送本地测试事件（不访问学习通）");test.setOnClickListener(v->sendTest());box.addView(test);
    Button refresh=new Button(this);refresh.setText("刷新连接状态");refresh.setOnClickListener(v->refresh());box.addView(refresh);
    setContentView(box);refresh();
  }
  protected void onResume(){super.onResume();if(status!=null)refresh();}
  void refresh(){
    boolean paired=new File(getFilesDir(),"pairing.json").isFile();
    String access=Settings.Secure.getString(getContentResolver(),"enabled_notification_listeners");
    boolean enabled=access!=null&&access.contains(getPackageName()+"/");
    File[] pending=new File(getFilesDir(),"pending").listFiles((d,n)->n.endsWith(".json"));
    File[] review=new File(getFilesDir(),"review").listFiles((d,n)->n.endsWith(".json"));
    status.setText("\n本机配对："+(paired?"已配置":"未配置")+"\n通知使用权："+(enabled?"已开启":"未开启")+"\n待发送："+(pending==null?0:pending.length)+" 条；待检查："+(review==null?0:review.length)+" 条\n"+getSharedPreferences("status",0).getString("last","尚无事件")+"\n");
  }
  void sendTest(){new Thread(()->{try{
    JSONObject e=new JSONObject();e.put("package",getPackageName());e.put("test",true);e.put("title","本地通知桥测试");e.put("text","测试通道，不代表真实学习通推送");e.put("posted_at",System.currentTimeMillis());e.put("notification_key","self-test-"+System.currentTimeMillis());
    CourseNotificationService.deliver(this,e);
  }catch(Exception ex){getSharedPreferences("status",0).edit().putString("last","测试失败："+ex.getClass().getSimpleName()).apply();}runOnUiThread(()->refresh());},"course-test").start();}
}
