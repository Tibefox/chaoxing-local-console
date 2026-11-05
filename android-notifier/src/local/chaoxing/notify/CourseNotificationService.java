package local.chaoxing.notify;
import android.app.Notification;
import android.content.Context;
import android.os.Bundle;
import android.service.notification.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.concurrent.*;
import org.json.*;

public class CourseNotificationService extends NotificationListenerService {
  static final int MAX_PAYLOAD_BYTES=32768;
  static class RejectedEvent extends IOException {
    RejectedEvent(String reason){super(reason);}
  }
  final ScheduledExecutorService worker=Executors.newSingleThreadScheduledExecutor();
  public void onCreate(){super.onCreate();worker.scheduleWithFixedDelay(()->flush(),1,10,TimeUnit.SECONDS);}
  public void onListenerConnected(){
    getSharedPreferences("status",0).edit().putString("last","通知服务已连接，等待学习通通知").apply();
    try{for(StatusBarNotification s:getActiveNotifications())capture(s,true);}catch(Exception ignored){}
  }
  public void onNotificationPosted(StatusBarNotification s){capture(s,false);}
  void capture(StatusBarNotification s,boolean replayed){
    if(!"com.chaoxing.mobile".equals(s.getPackageName()))return;
    try{
      Notification n=s.getNotification();Bundle b=n.extras;JSONObject e=new JSONObject();
      e.put("package",s.getPackageName());e.put("notification_key",s.getKey());e.put("posted_at",s.getPostTime());e.put("replayed",replayed);
      e.put("title",clip(b.getCharSequence(Notification.EXTRA_TITLE)));e.put("text",clip(b.getCharSequence(Notification.EXTRA_TEXT)));e.put("big_text",clip(b.getCharSequence(Notification.EXTRA_BIG_TEXT)));
      CharSequence[] lines=b.getCharSequenceArray(Notification.EXTRA_TEXT_LINES);JSONArray texts=new JSONArray();if(lines!=null)for(int i=0;i<Math.min(lines.length,12);i++)texts.put(clip(lines[i]));e.put("lines",texts);
      JSONArray links=new JSONArray();for(String key:new String[]{"url","webUrl","weburl","link","jumpUrl"}){Object v=b.get(key);if(v instanceof String&&((String)v).length()<3000)links.put(v);}e.put("links",links);
      worker.submit(()->{try{enqueue(e);flush();}catch(Exception ex){getSharedPreferences("status",0).edit().putString("last","通知未能入队："+ex.getClass().getSimpleName()).apply();}});
    }catch(Exception ignored){}
  }
  static String clip(CharSequence s){if(s==null)return "";String v=s.toString();return v.substring(0,Math.min(v.length(),4000));}
  void enqueue(JSONObject event)throws Exception {
    File dir=new File(getFilesDir(),"pending");dir.mkdirs();
    String identity=event.getString("notification_key")+event.getLong("posted_at")+event.optString("title")+event.optString("text")+event.optString("big_text")+event.optJSONArray("lines")+event.optJSONArray("links");
    byte[] digest=MessageDigest.getInstance("SHA-256").digest(identity.getBytes(StandardCharsets.UTF_8));StringBuilder id=new StringBuilder();for(byte h:digest)id.append(String.format("%02x",h));
    File target=new File(dir,id+".json");if(target.exists()||new File(new File(getFilesDir(),"review"),target.getName()).exists())return;
    File[] files=dir.listFiles((d,n)->n.endsWith(".json"));
    File[] review=new File(getFilesDir(),"review").listFiles((d,n)->n.endsWith(".json"));
    if((files==null?0:files.length)+(review==null?0:review.length)>=500)throw new IOException("本地队列已满，请连接电脑接收端");
    File tmp=new File(dir,id+".tmp");try(FileOutputStream out=new FileOutputStream(tmp)){out.write(event.toString().getBytes(StandardCharsets.UTF_8));out.getFD().sync();}
    if(!tmp.renameTo(target))throw new IOException("queue rename");
  }
  void flush(){
    File[] files=new File(getFilesDir(),"pending").listFiles((d,n)->n.endsWith(".json"));if(files==null)return;
    java.util.Arrays.sort(files,java.util.Comparator.comparingLong(File::lastModified));
    for(File file:files){if(Thread.currentThread().isInterrupted())return;try{
      byte[] bytes=new byte[(int)file.length()];try(DataInputStream in=new DataInputStream(new FileInputStream(file))){in.readFully(bytes);}
      JSONObject event;
      try{event=new JSONObject(new String(bytes,StandardCharsets.UTF_8));}
      catch(JSONException ex){throw new RejectedEvent("invalid queued event");}
      deliver(this,event);
      if(!file.delete())throw new IOException("queue delete failed");
    }catch(RejectedEvent ex){
      // Preserve the original event for review; a bad event must not block later ones.
      File review=new File(getFilesDir(),"review");review.mkdirs();
      if(!file.renameTo(new File(review,file.getName()))){
        getSharedPreferences("status",0).edit().putString("last","待检查事件无法归档；原始文件保留在待发队列").apply();return;
      }
      getSharedPreferences("status",0).edit().putString("last","一条通知超过大小限制或格式被拒绝；已保存在本机待检查").apply();
    }catch(Exception ex){getSharedPreferences("status",0).edit().putString("last","本机转发未完成；待发通知已保存，10秒后重试本地连接").apply();return;}}
  }
  static void deliver(Context c,JSONObject event)throws Exception {
    File config=new File(c.getFilesDir(),"pairing.json");byte[] data;
    try(FileInputStream in=new FileInputStream(config)){ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] buf=new byte[1024];int n;while((n=in.read(buf))!=-1){out.write(buf,0,n);if(out.size()>8192)throw new IOException("config too large");}data=out.toByteArray();}
    JSONObject p=new JSONObject(new String(data,StandardCharsets.UTF_8));String endpoint=p.getString("endpoint");
    if(!"http://127.0.0.1:29688/v1/notifications".equals(endpoint))throw new IOException("endpoint denied");
    String identity=event.toString();byte[] hash=MessageDigest.getInstance("SHA-256").digest(identity.getBytes(StandardCharsets.UTF_8));StringBuilder id=new StringBuilder();for(byte h:hash)id.append(String.format("%02x",h));event.put("event_id",id.toString());
    byte[] payload=event.toString().getBytes(StandardCharsets.UTF_8);
    if(payload.length>MAX_PAYLOAD_BYTES)throw new RejectedEvent("payload too large");
    HttpURLConnection conn=(HttpURLConnection)new URL(endpoint).openConnection();
    conn.setInstanceFollowRedirects(false);
    conn.setConnectTimeout(3000);conn.setReadTimeout(5000);conn.setRequestMethod("POST");conn.setDoOutput(true);conn.setRequestProperty("Content-Type","application/json; charset=utf-8");conn.setRequestProperty("Authorization","Bearer "+p.getString("token"));
    try{conn.setFixedLengthStreamingMode(payload.length);try(OutputStream out=conn.getOutputStream()){out.write(payload);}int code=conn.getResponseCode();
      if(code==400||code==413)throw new RejectedEvent("host HTTP "+code);
      if(code!=200)throw new IOException("host HTTP "+code);
      // A success code alone is not proof that this receiver accepted the event.
      try(InputStream in=conn.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
        byte[] buf=new byte[1024];int count;
        while((count=in.read(buf))!=-1){out.write(buf,0,count);if(out.size()>8192)throw new IOException("receipt too large");}
        try{if(!Boolean.TRUE.equals(new JSONObject(new String(out.toByteArray(),StandardCharsets.UTF_8)).opt("accepted")))throw new IOException("event not accepted");}
        catch(JSONException ex){throw new IOException("invalid receipt",ex);}
      }
      c.getSharedPreferences("status",0).edit().putString("last","已转发通知至本机："+new java.text.SimpleDateFormat("HH:mm:ss").format(new java.util.Date())).apply();}
    finally{conn.disconnect();}
  }
  public void onDestroy(){worker.shutdownNow();super.onDestroy();}
}
