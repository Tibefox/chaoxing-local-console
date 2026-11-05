package local.chaoxing.uiread;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.Activity;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.Base64;
import android.util.Xml;
import android.view.accessibility.AccessibilityNodeInfo;
import org.xmlpull.v1.XmlSerializer;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.List;
import org.json.JSONArray;

public class Reader extends Instrumentation {
    private static final String APP = "com.chaoxing.mobile";
    private static final String GROUP = "软件工程(唐班)";
    private int count;
    private String alertAction;
    private String cardRequest;
    private String scrollGroup;
    private String choiceRequest;
    private String loginRequest;
    private String joinedScroll;

    @Override public void onCreate(Bundle args) {
        super.onCreate(args);
        alertAction = args.getString("quizAlert", "");
        cardRequest = args.getString("openGroupCard", "");
        scrollGroup = args.getString("scrollGroup", "");
        choiceRequest = args.getString("selectChoice", "");
        loginRequest = args.getString("loginCredential", "");
        joinedScroll = args.getString("scrollJoined", "");
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            UiAutomation ui = getUiAutomation(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);
            AccessibilityServiceInfo info = ui.getServiceInfo();
            info.flags |= AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
            ui.setServiceInfo(info);
            AccessibilityNodeInfo root = null;
            for (int i = 0; i < 8 && root == null; i++) {
                root = ui.getRootInActiveWindow();
                if (root == null) SystemClock.sleep(80);
            }
            if (root == null) throw new IOException("no foreground tree");
            String loginAction = "none";
            if (!loginRequest.isEmpty()) {
                loginAction = login(ui, root);
                root.recycle();
                root = ui.getRootInActiveWindow();
                if (root == null) throw new IOException("no foreground tree");
            }
            String click = "none";
            boolean opened = !cardRequest.isEmpty() && openGroupCard(root);
            if (opened) android.util.Log.i("CourseTestAudit", "target_group_card_opened");
            boolean scrolled = !scrollGroup.isEmpty() && scrollChat(root);
            boolean joinedScrolled = "forward".equals(joinedScroll) && scrollJoined(root);
            boolean selected = !choiceRequest.isEmpty() && selectChoice(root);
            if (selected) android.util.Log.i("CourseTestAudit", "activity_choice_clicked");
            if (!alertAction.isEmpty()) {
                int originalWindow = root.getWindowId(); // Only comparable within this connection.
                click = actOnQuizAlert(root);
                if ("clicked".equals(click)) {
                    // Ignore the outgoing window while its dismissal animation runs.
                    for (int i = 0; i < 8; i++) {
                        SystemClock.sleep(100);
                        AccessibilityNodeInfo fresh = ui.getRootInActiveWindow();
                        if (fresh == null) continue;
                        root.recycle();
                        root = fresh;
                        if (root.getWindowId() != originalWindow || !isActivityAlert(root)) break;
                    }
                    boolean gone = root.getWindowId() != originalWindow || !isActivityAlert(root);
                    android.util.Log.i("CourseTestAudit", "activity_cancel_clicked dismissed=" + gone);
                }
            }
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            XmlSerializer xml = Xml.newSerializer();
            xml.setOutput(bytes, "UTF-8");
            xml.startDocument("UTF-8", true);
            xml.startTag(null, "hierarchy");
            xml.attribute(null, "window", String.valueOf(root.getWindowId()));
            xml.attribute(null, "readerVersion", "3");
            xml.attribute(null, "loginAction", loginAction);
            xml.attribute(null, "alertAction", click);
            xml.attribute(null, "cardAction", opened ? "clicked" : "none");
            xml.attribute(null, "scrolled", String.valueOf(scrolled));
            xml.attribute(null, "joinedScrolled", String.valueOf(joinedScrolled));
            xml.attribute(null, "choiceAction", selected ? "clicked" : "changed");
            node(root, xml, 0);
            xml.endTag(null, "hierarchy");
            xml.endDocument();
            result.putString("tree", Base64.encodeToString(bytes.toByteArray(), Base64.NO_WRAP));
            finish(Activity.RESULT_OK, result);
        } catch (Exception ex) {
            result.putString("readerError", ex.getClass().getSimpleName());
            finish(Activity.RESULT_CANCELED, result);
        }
    }

    private String attr(CharSequence s) { return s == null ? "" : s.toString(); }

    private String groupName(String value) {
        return java.text.Normalizer.normalize(value,java.text.Normalizer.Form.NFKC).trim();
    }

    private boolean targetGroup(AccessibilityNodeInfo root) {
        List<AccessibilityNodeInfo> titles=root.findAccessibilityNodeInfosByViewId(APP+":id/tvTitle2");
        try {return titles.size()==1 && titles.get(0).isVisibleToUser()
            && APP.contentEquals(attr(titles.get(0).getPackageName()))
            && GROUP.equals(groupName(attr(titles.get(0).getText())));}
        finally {for(AccessibilityNodeInfo n:titles)n.recycle();}
    }

    private boolean scrollJoined(AccessibilityNodeInfo root) {
        AccessibilityNodeInfo title=loginNode(root,"tvTitle"),list=loginNode(root,"listView");
        try {return title!=null && list!=null && groupName(attr(title.getText())).matches("我加入的群聊(?:\\(\\d+\\))?")
            && list.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD);}
        finally {if(title!=null)title.recycle();if(list!=null)list.recycle();}
    }

    private AccessibilityNodeInfo loginNode(AccessibilityNodeInfo root, String id) {
        List<AccessibilityNodeInfo> list=root.findAccessibilityNodeInfosByViewId(APP+":id/"+id);
        AccessibilityNodeInfo result=null;
        if (list.size()==1 && APP.contentEquals(attr(list.get(0).getPackageName())) && list.get(0).isVisibleToUser())
            result=AccessibilityNodeInfo.obtain(list.get(0));
        for (AccessibilityNodeInfo n:list) n.recycle();
        return result;
    }

    private boolean challenge(AccessibilityNodeInfo root) {
        if (!APP.contentEquals(attr(root.getPackageName()))) return true;
        String text=root.isEditable() ? "" : attr(root.getText())+attr(root.getContentDescription());
        if (text.matches("(?s).*(安全验证|身份验证|验证身份|拖动.*滑块|请输入验证码|短信验证码|图形验证码|人脸识别|密码错误|密码不正确|账号被锁定).*")) return true;
        for (int i=0;i<root.getChildCount();i++) {
            AccessibilityNodeInfo n=root.getChild(i);
            if (n!=null) {try {if(challenge(n)) return true;} finally {n.recycle();}}
        }
        return false;
    }

    private boolean unchecked(AccessibilityNodeInfo root) {
        if (root.isVisibleToUser() && root.isCheckable() && !root.isChecked()) return true;
        for(int i=0;i<root.getChildCount();i++) {
            AccessibilityNodeInfo n=root.getChild(i);
            if(n!=null) {try {if(unchecked(n))return true;} finally {n.recycle();}}
        }
        return false;
    }

    private boolean loginForm(AccessibilityNodeInfo root) {
        if (root==null || !APP.contentEquals(attr(root.getPackageName())) || challenge(root)) return false;
        AccessibilityNodeInfo container=loginNode(root,"rl_login_container"),account=loginNode(root,"et_account"),
            password=loginNode(root,"et_password"),button=loginNode(root,"btn_login");
        try {
            return container!=null && account!=null && password!=null && button!=null
                && account.isEditable() && !account.isPassword() && password.isEditable() && password.isPassword()
                && account.isEnabled() && password.isEnabled() && button.isClickable()
                && "登录".equals(attr(button.getText()).replace(" ",""));
        } finally {for(AccessibilityNodeInfo n:new AccessibilityNodeInfo[]{container,account,password,button})if(n!=null)n.recycle();}
    }

    private boolean setSecret(AccessibilityNodeInfo field,String value) {
        Bundle args=new Bundle();args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,value);
        try{return field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,args);}
        finally{args.clear();}
    }

    private String login(UiAutomation ui,AccessibilityNodeInfo initial) throws Exception {
        if(!loginForm(initial))return "unsupported";
        AccessibilityNodeInfo privacy=loginNode(initial,"user_agreement_tips");
        if(privacy!=null) {try {if(unchecked(privacy))return "agreement_required";} finally {privacy.recycle();}}
        org.json.JSONObject request=new org.json.JSONObject(new String(Base64.decode(loginRequest,Base64.DEFAULT),"UTF-8"));
        int port=request.getInt("port");String token=request.getString("token");
        if(port<1024 || port>65535 || !token.matches("[A-Za-z0-9_-]{40,64}"))return "invalid";
        java.net.HttpURLConnection connection=(java.net.HttpURLConnection)new java.net.URL("http://127.0.0.1:"+port+"/credential").openConnection();
        connection.setConnectTimeout(3000);connection.setReadTimeout(3000);connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Authorization","Bearer "+token);
        connection.setRequestProperty("Cache-Control","no-store");
        org.json.JSONObject credentials;
        byte[] data=null;
        try {
            if(connection.getResponseCode()!=200)return "unavailable";
            ByteArrayOutputStream buffer=new ByteArrayOutputStream();
            try(java.io.InputStream input=connection.getInputStream()) {
                byte[] chunk=new byte[1024];int size;
                while((size=input.read(chunk))!=-1) {if(buffer.size()+size>16384)return "invalid";buffer.write(chunk,0,size);}
                java.util.Arrays.fill(chunk,(byte)0);
            }
            data=buffer.toByteArray();credentials=new org.json.JSONObject(new String(data,"UTF-8"));buffer.reset();
        } finally {connection.disconnect();if(data!=null)java.util.Arrays.fill(data,(byte)0);}
        AccessibilityNodeInfo root=ui.getRootInActiveWindow();
        try {
            if(root==null || root.getWindowId()!=initial.getWindowId() || !loginForm(root))return "changed";
            String username=credentials.getString("username"),password=credentials.getString("password");
            if(username.isEmpty() || username.length()>256 || password.isEmpty() || password.length()>1024)return "invalid";
            AccessibilityNodeInfo account=loginNode(root,"et_account"),pwd=loginNode(root,"et_password");
            try {
                if(!setSecret(account,username) || !setSecret(pwd,password)) {setSecret(pwd,"");return "failed";}
            } finally {account.recycle();pwd.recycle();}
            SystemClock.sleep(150);
            AccessibilityNodeInfo fresh=ui.getRootInActiveWindow();
            try {
                if(fresh==null || fresh.getWindowId()!=root.getWindowId() || !loginForm(fresh))return "changed";
                AccessibilityNodeInfo button=loginNode(fresh,"btn_login");
                try {return button.isEnabled() && button.performAction(AccessibilityNodeInfo.ACTION_CLICK) ? "submitted" : "failed";}
                finally {button.recycle();}
            } finally {if(fresh!=null)fresh.recycle();}
        } finally {credentials.remove("username");credentials.remove("password");if(root!=null)root.recycle();}
    }

    private AccessibilityNodeInfo exact(AccessibilityNodeInfo root, String id, String label) {
        List<AccessibilityNodeInfo> found = root.findAccessibilityNodeInfosByViewId(APP + ":id/" + id);
        if (found.size() == 1) {
            AccessibilityNodeInfo n = found.get(0);
            if (APP.contentEquals(attr(n.getPackageName())) && n.isVisibleToUser()
                    && label.contentEquals(attr(n.getText()))) return n;
        }
        for (AccessibilityNodeInfo n : found) n.recycle();
        return null;
    }

    private boolean has(AccessibilityNodeInfo root, String id, String label) {
        AccessibilityNodeInfo n = exact(root, id, label);
        if (n == null) return false;
        n.recycle();
        return true;
    }

    private boolean isActivityAlert(AccessibilityNodeInfo root) {
        return has(root, "tvTitle", "课程活动") && has(root, "btnOk", "查看") && has(root, "btnCancel", "取消");
    }

    private String actOnQuizAlert(AccessibilityNodeInfo root) {
        if (!isActivityAlert(root)) return "changed";
        if (!"dismiss".equals(alertAction)) return "invalid";
        AccessibilityNodeInfo button = exact(root, "btnCancel", "取消");
        if (button == null) return "changed";
        try {
            return button.isEnabled() && button.performAction(AccessibilityNodeInfo.ACTION_CLICK) ? "clicked" : "failed";
        } finally {
            button.recycle();
        }
    }

    private boolean openGroupCard(AccessibilityNodeInfo root) throws Exception {
        if (!targetGroup(root) || isActivityAlert(root)) return false;
        JSONArray expected = new JSONArray(new String(Base64.decode(cardRequest, Base64.DEFAULT), "UTF-8"));
        List<AccessibilityNodeInfo> histories = root.findAccessibilityNodeInfosByViewId(APP + ":id/lv_chat_his");
        try {
            if (histories.size() != 1) return false;
            List<AccessibilityNodeInfo> cards = histories.get(0).findAccessibilityNodeInfosByViewId(APP + ":id/v_attachment_message");
            try {
                for (AccessibilityNodeInfo card : cards) {
                    Rect rect = new Rect();
                    card.getBoundsInScreen(rect);
                    if (!rect.toShortString().equals(expected.getString(0))
                            || !has(card, "tvContent", expected.getString(2))) continue;
                    AccessibilityNodeInfo n = exact(card, "tvTitle", expected.getString(1));
                    while (n != null) {
                        boolean boundary = n.equals(card);
                        boolean clicked = n.isEnabled() && n.isClickable()
                                && n.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                        AccessibilityNodeInfo parent = clicked || boundary ? null : n.getParent();
                        n.recycle();
                        if (clicked) return true;
                        n = parent;
                    }
                }
            } finally { for (AccessibilityNodeInfo card : cards) card.recycle(); }
        } finally { for (AccessibilityNodeInfo h : histories) h.recycle(); }
        return false;
    }

    private boolean scrollChat(AccessibilityNodeInfo root) {
        if (!targetGroup(root) || isActivityAlert(root)) return false;
        if (!"older".equals(scrollGroup) && !"newer".equals(scrollGroup)) return false;
        List<AccessibilityNodeInfo> histories = root.findAccessibilityNodeInfosByViewId(APP + ":id/lv_chat_his");
        try {
            return histories.size() == 1 && histories.get(0).performAction("older".equals(scrollGroup)
                    ? AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD : AccessibilityNodeInfo.ACTION_SCROLL_FORWARD);
        } finally { for (AccessibilityNodeInfo h : histories) h.recycle(); }
    }

    private boolean containsExactText(AccessibilityNodeInfo root, String expected) {
        if (!APP.contentEquals(attr(root.getPackageName()))) return false;
        if (expected.equals(attr(root.getText())) || expected.equals(attr(root.getContentDescription()))) return true;
        for (int i=0;i<root.getChildCount();i++) {
            AccessibilityNodeInfo child=root.getChild(i);
            if (child==null) continue;
            boolean found=containsExactText(child,expected);
            child.recycle();
            if (found) return true;
        }
        return false;
    }

    private void choiceNodes(AccessibilityNodeInfo root, JSONArray request,
                             java.util.ArrayList<AccessibilityNodeInfo> matches) throws Exception {
        if (!APP.contentEquals(attr(root.getPackageName()))) return;
        Rect rect=new Rect();root.getBoundsInScreen(rect);
        String value=attr(root.getText()).trim();
        if (value.isEmpty()) value=attr(root.getContentDescription()).trim();
        if (root.isVisibleToUser() && root.isEnabled() && root.isClickable()
                && request.getString(2).equals(value) && request.getString(3).equals(attr(root.getClassName()))
                && request.getString(4).equals(rect.toShortString())) matches.add(AccessibilityNodeInfo.obtain(root));
        for (int i=0;i<root.getChildCount();i++) {
            AccessibilityNodeInfo child=root.getChild(i);
            if (child!=null) {try {choiceNodes(child,request,matches);} finally {child.recycle();}}
        }
    }

    private boolean selectChoice(AccessibilityNodeInfo root) throws Exception {
        if (isActivityAlert(root)) return false;
        JSONArray request=new JSONArray(new String(Base64.decode(choiceRequest,Base64.DEFAULT),"UTF-8"));
        String title=request.getString(0);
        if (!("投票".equals(title)||"随堂练习".equals(title)) || !has(root,"toolbar_tv_title",title)
                || !containsExactText(root,request.getString(1))) return false;
        java.util.ArrayList<AccessibilityNodeInfo> matches=new java.util.ArrayList<>();
        try {
            choiceNodes(root,request,matches);
            return matches.size()==1 && matches.get(0).performAction(AccessibilityNodeInfo.ACTION_CLICK);
        } finally {for (AccessibilityNodeInfo n:matches) n.recycle();}
    }

    private void node(AccessibilityNodeInfo n, XmlSerializer xml, int depth) throws IOException {
        try {
            if (!APP.contentEquals(attr(n.getPackageName())) || depth > 60 || ++count > 1800) return;
            xml.startTag(null, "node");
            xml.attribute(null, "package", APP);
            xml.attribute(null, "class", attr(n.getClassName()));
            xml.attribute(null, "resource-id", attr(n.getViewIdResourceName()));
            AccessibilityNodeInfo.CollectionItemInfo item = n.getCollectionItemInfo();
            if (item != null) xml.attribute(null, "row", String.valueOf(item.getRowIndex()));
            if ((APP + ":id/lv_chat_his").equals(n.getViewIdResourceName())) {
                xml.attribute(null,"older",Boolean.toString(n.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_BACKWARD)));
                xml.attribute(null,"newer",Boolean.toString(n.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_FORWARD)));
            }
            xml.attribute(null, "text", (n.isPassword() || n.isEditable()) ? "" : attr(n.getText()));
            xml.attribute(null, "content-desc", (n.isPassword() || n.isEditable()) ? "" : attr(n.getContentDescription()));
            xml.attribute(null, "password", Boolean.toString(n.isPassword()));
            xml.attribute(null, "editable", Boolean.toString(n.isEditable()));
            xml.attribute(null, "clickable", Boolean.toString(n.isClickable()));
            xml.attribute(null, "checked", Boolean.toString(n.isChecked()));
            xml.attribute(null, "checkable", Boolean.toString(n.isCheckable()));
            xml.attribute(null, "selected", Boolean.toString(n.isSelected()));
            xml.attribute(null, "enabled", Boolean.toString(n.isEnabled()));
            xml.attribute(null, "visible", Boolean.toString(n.isVisibleToUser()));
            Rect rect = new Rect();
            if (n.isVisibleToUser()) n.getBoundsInScreen(rect);
            xml.attribute(null, "bounds", "["+rect.left+","+rect.top+"]["+rect.right+","+rect.bottom+"]");
            for (int i = 0; i < n.getChildCount(); i++) {
                AccessibilityNodeInfo child = n.getChild(i);
                if (child != null) node(child, xml, depth+1);
            }
            xml.endTag(null, "node");
        } finally {
            n.recycle();
        }
    }
}
