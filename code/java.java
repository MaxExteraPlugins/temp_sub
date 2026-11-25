package com.exteraplugins.temp_sub;

import android.app.Activity;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.text.TextUtils;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;
import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.ImageReceiver;
import org.telegram.messenger.LocaleController;
import org.telegram.ui.ActionBar.BottomSheet;
import org.telegram.ui.ActionBar.Theme;
import org.telegram.ui.Cells.CheckBoxCell;
import org.telegram.ui.Cells.DialogCell;
import org.telegram.ui.Components.LayoutHelper;
import org.telegram.ui.Components.NumberPicker;
import org.telegram.ui.Components.ScaleStateListAnimator;

import java.lang.reflect.Field;
import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;

public class Main {
    private static final String TAG = "TempSub";
    public static final String VERSION = "3.1.5";
    public static final int VERSION_CODE = 315;

    private static final AtomicBoolean hooksInstalled = new AtomicBoolean(false);
    private static final ConcurrentHashMap<Long, Long> subsByDialogId = new ConcurrentHashMap<>();
    private static volatile boolean badgeEnabled = true;

    private static Field avatarImageField;
    private static boolean avatarFieldSearched = false;
    private static Paint badgeBgPaint;
    private static Paint badgeBorderPaint;
    private static Paint badgeTextPaint;
    private static final RectF badgeRect = new RectF();

    // Result bridge for pyShowScheduleSheet -> Python Runnable callback
    public static volatile long lastResultLeaveAt = 0L;
    public static volatile boolean lastResultArchive = true;
    public static volatile boolean lastResultConfirmed = false;

    public static void load() {
        Log.d(TAG, "DEX module loaded v" + VERSION);
        installDialogCellHook();
    }

    public static void pySetBadgeEnabled(boolean enabled) {
        badgeEnabled = enabled;
    }

    public static void pySyncSubs(String json) {
        ConcurrentHashMap<Long, Long> next = new ConcurrentHashMap<>();
        if (json != null && !json.isEmpty()) {
            try {
                JSONArray arr = new JSONArray(json);
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject obj = arr.optJSONObject(i);
                    if (obj == null) continue;
                    long channelId = obj.optLong("channel_id", 0L);
                    long leaveAt = obj.optLong("leave_at", 0L);
                    boolean joined = obj.optBoolean("joined", true);
                    if (channelId > 0 && leaveAt > 0 && joined) {
                        next.put(-channelId, leaveAt);
                    }
                }
            } catch (Throwable t) {
                Log.e(TAG, "pySyncSubs parse error", t);
            }
        }
        subsByDialogId.clear();
        subsByDialogId.putAll(next);
    }

    private static boolean isRuLocale() {
        try {
            String lang = LocaleController.getInstance().getCurrentLocale().getLanguage();
            if (lang != null) {
                lang = lang.toLowerCase(Locale.US);
                return "ru".equals(lang) || "be".equals(lang) || "uk".equals(lang) || "kk".equals(lang);
            }
        } catch (Throwable ignored) {
        }
        return true;
    }

    private static void installDialogCellHook() {
        if (!hooksInstalled.compareAndSet(false, true)) {
            return;
        }
        try {
            XposedBridge.hookAllMethods(DialogCell.class, "onDraw", new XC_MethodHook() {
                @Override
                public void afterHookedMethod(MethodHookParam param) {
                    if (!badgeEnabled || subsByDialogId.isEmpty()) {
                        return;
                    }
                    Object obj = param.thisObject;
                    if (!(obj instanceof DialogCell) || param.args == null || param.args.length == 0) {
                        return;
                    }
                    if (!(param.args[0] instanceof Canvas)) {
                        return;
                    }
                    DialogCell cell = (DialogCell) obj;
                    long dialogId = cell.getDialogId();
                    if (dialogId >= 0) {
                        return;
                    }
                    Long leaveAtObj = subsByDialogId.get(dialogId);
                    if (leaveAtObj == null) {
                        return;
                    }
                    drawRemainingBadge(cell, (Canvas) param.args[0], leaveAtObj);
                }
            });
        } catch (Throwable t) {
            Log.e(TAG, "installDialogCellHook error", t);
        }
    }

    private static ImageReceiver getAvatarImage(DialogCell cell) {
        try {
            if (!avatarFieldSearched) {
                avatarFieldSearched = true;
                Field f = DialogCell.class.getDeclaredField("avatarImage");
                f.setAccessible(true);
                avatarImageField = f;
            }
            if (avatarImageField != null) {
                Object val = avatarImageField.get(cell);
                if (val instanceof ImageReceiver) {
                    return (ImageReceiver) val;
                }
            }
        } catch (Throwable ignored) {
        }
        return null;
    }

    private static String formatCompactRemaining(long remSec, boolean ru) {
        if (remSec <= 0) {
            return ru ? "0м" : "0m";
        }
        long mins = remSec / 60L;
        if (mins >= 1440L) {
            return (mins / 1440L) + (ru ? "д" : "d");
        }
        if (mins >= 60L) {
            return (mins / 60L) + (ru ? "ч" : "h");
        }
        return Math.max(1L, mins) + (ru ? "м" : "m");
    }

    private static void drawRemainingBadge(DialogCell cell, Canvas canvas, long leaveAt) {
        ImageReceiver img = getAvatarImage(cell);
        if (img == null) {
            return;
        }
        float w = img.getImageWidth();
        if (w <= 0f) {
            return;
        }

        long nowSec = System.currentTimeMillis() / 1000L;
        long rem = leaveAt - nowSec;
        boolean ru = isRuLocale();
        String text = formatCompactRemaining(rem, ru);

        if (badgeBgPaint == null) {
            badgeBgPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
            badgeBgPaint.setStyle(Paint.Style.FILL);
        }
        if (badgeBorderPaint == null) {
            badgeBorderPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
            badgeBorderPaint.setStyle(Paint.Style.FILL);
        }
        if (badgeTextPaint == null) {
            badgeTextPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
            badgeTextPaint.setTypeface(AndroidUtilities.bold());
            badgeTextPaint.setTextSize(AndroidUtilities.dp(10));
        }

        int pillColor;
        if (rem <= 3600L) {
            pillColor = 0xFFE53935;
        } else if (rem <= 86400L) {
            pillColor = 0xFFFB8C00;
        } else {
            pillColor = Theme.getColor(Theme.key_featuredStickers_addButton);
        }

        int borderColor;
        try {
            borderColor = Theme.getColor(Theme.key_windowBackgroundWhite);
        } catch (Throwable t) {
            borderColor = Color.WHITE;
        }

        float cx = img.getImageX() + w / 2f;
        float cy = img.getImageY() + w / 2f;
        float r = w / 2f + AndroidUtilities.dp(2);

        float tw = badgeTextPaint.measureText(text);
        float padH = AndroidUtilities.dp(4.5f);
        float pillH = AndroidUtilities.dp(14);
        float pillW = tw + padH * 2f;

        float right = Math.min(cx + r + AndroidUtilities.dp(2), cell.getWidth() - AndroidUtilities.dp(2));
        float bottom = cy + r + AndroidUtilities.dp(1);
        float left = right - pillW;
        float top = bottom - pillH;
        float radius = pillH / 2f;

        float border = AndroidUtilities.dp(1.5f);
        badgeBorderPaint.setColor(borderColor);
        badgeRect.set(left - border, top - border, right + border, bottom + border);
        canvas.drawRoundRect(badgeRect, radius + border, radius + border, badgeBorderPaint);

        badgeBgPaint.setColor(pillColor);
        badgeRect.set(left, top, right, bottom);
        canvas.drawRoundRect(badgeRect, radius, radius, badgeBgPaint);

        badgeTextPaint.setColor(Color.WHITE);
        Paint.FontMetrics fm = badgeTextPaint.getFontMetrics();
        float textY = (top + bottom) / 2f - (fm.ascent + fm.descent) / 2f;
        canvas.drawText(text, left + padH, textY, badgeTextPaint);
    }

    // ---------------------------------------------------------------
    // Native Telegram BottomSheet date/time picker
    // ---------------------------------------------------------------

    public static void pyShowScheduleSheet(
            final Activity activity,
            final String title,
            final long initialLeaveAt,
            final boolean initialArchive,
            final boolean isEdit,
            final Runnable onConfirm
    ) {
        if (activity == null) {
            return;
        }
        lastResultConfirmed = false;
        final boolean ru = isRuLocale();

        final BottomSheet sheet = new BottomSheet(activity, false);
        sheet.setApplyBottomPadding(false);

        final int textBlack = Theme.getColor(Theme.key_dialogTextBlack);
        final int textGray = Theme.getColor(Theme.key_dialogTextGray3);
        final int accentColor = Theme.getColor(Theme.key_featuredStickers_addButton);
        final int accentText = Theme.getColor(Theme.key_featuredStickers_buttonText);
        final int chipNormalBg = Theme.multAlpha(textBlack, 0.07f);

        LinearLayout root = new LinearLayout(activity);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(0, AndroidUtilities.dp(6), 0, AndroidUtilities.dp(16));

        // Header
        LinearLayout header = new LinearLayout(activity);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(AndroidUtilities.dp(22), AndroidUtilities.dp(10), AndroidUtilities.dp(22), AndroidUtilities.dp(6));

        TextView headerTitle = new TextView(activity);
        headerTitle.setText(isEdit
                ? (ru ? "Изменить время отписки" : "Edit unsubscribe time")
                : (ru ? "Временная подписка" : "Temporary subscription"));
        headerTitle.setTextColor(textBlack);
        headerTitle.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 20);
        headerTitle.setTypeface(AndroidUtilities.bold());
        header.addView(headerTitle);

        if (title != null && !title.isEmpty()) {
            TextView subTitle = new TextView(activity);
            subTitle.setText(title);
            subTitle.setTextColor(textGray);
            subTitle.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 14);
            subTitle.setSingleLine(true);
            subTitle.setEllipsize(TextUtils.TruncateAt.END);
            subTitle.setPadding(0, AndroidUtilities.dp(2), 0, 0);
            header.addView(subTitle);
        }
        root.addView(header);

        // Quick preset chips
        final long[] presetSeconds = new long[]{
                3600L, 6L * 3600L, 86400L, 2L * 86400L, 3L * 86400L,
                7L * 86400L, 14L * 86400L, 30L * 86400L, 90L * 86400L
        };
        final String[] presetLabels = ru
                ? new String[]{"1ч", "6ч", "1д", "2д", "3д", "7д", "14д", "30д", "90д"}
                : new String[]{"1h", "6h", "1d", "2d", "3d", "7d", "14d", "30d", "90d"};

        HorizontalScrollView chipsScroll = new HorizontalScrollView(activity);
        chipsScroll.setHorizontalScrollBarEnabled(false);
        chipsScroll.setClipToPadding(false);
        chipsScroll.setPadding(AndroidUtilities.dp(18), AndroidUtilities.dp(6), AndroidUtilities.dp(18), AndroidUtilities.dp(6));

        LinearLayout chipsRow = new LinearLayout(activity);
        chipsRow.setOrientation(LinearLayout.HORIZONTAL);
        chipsScroll.addView(chipsRow);
        root.addView(chipsScroll, LayoutHelper.createLinear(-1, -2));

        final TextView[] chipViews = new TextView[presetLabels.length];
        final int[] selectedChip = new int[]{-1};

        // Base day: midnight of today in local calendar
        final Calendar todayMidnight = Calendar.getInstance();
        todayMidnight.set(Calendar.HOUR_OF_DAY, 0);
        todayMidnight.set(Calendar.MINUTE, 0);
        todayMidnight.set(Calendar.SECOND, 0);
        todayMidnight.set(Calendar.MILLISECOND, 0);
        final long todayMidnightMs = todayMidnight.getTimeInMillis();

        final int maxDays = 365;
        final SimpleDateFormat dayFmt = new SimpleDateFormat("d MMM", ru ? new Locale("ru") : Locale.US);
        final SimpleDateFormat fullFmt = new SimpleDateFormat("dd.MM.yyyy HH:mm", ru ? new Locale("ru") : Locale.US);

        final NumberPicker dayPicker = new NumberPicker(activity);
        dayPicker.setMinValue(0);
        dayPicker.setMaxValue(maxDays);
        dayPicker.setWrapSelectorWheel(false);
        dayPicker.setItemCount(5);
        dayPicker.setTextColor(textBlack);
        dayPicker.setTextOffset(AndroidUtilities.dp(10));
        dayPicker.setFormatter(new NumberPicker.Formatter() {
            @Override
            public String format(int value) {
                if (value == 0) {
                    return ru ? "Сегодня" : "Today";
                }
                if (value == 1) {
                    return ru ? "Завтра" : "Tomorrow";
                }
                Calendar c = Calendar.getInstance();
                c.setTimeInMillis(todayMidnightMs + value * 86400000L);
                return dayFmt.format(c.getTime());
            }
        });

        final NumberPicker hourPicker = new NumberPicker(activity);
        hourPicker.setMinValue(0);
        hourPicker.setMaxValue(23);
        hourPicker.setAllItemsCount(24);
        hourPicker.setWrapSelectorWheel(true);
        hourPicker.setItemCount(5);
        hourPicker.setTextColor(textBlack);
        hourPicker.setTextOffset(-AndroidUtilities.dp(10));
        hourPicker.setFormatter(new NumberPicker.Formatter() {
            @Override
            public String format(int value) {
                return String.format(Locale.US, "%02d", value);
            }
        });

        final NumberPicker minutePicker = new NumberPicker(activity);
        minutePicker.setMinValue(0);
        minutePicker.setMaxValue(59);
        minutePicker.setAllItemsCount(60);
        minutePicker.setWrapSelectorWheel(true);
        minutePicker.setItemCount(5);
        minutePicker.setTextColor(textBlack);
        minutePicker.setTextOffset(-AndroidUtilities.dp(24));
        minutePicker.setFormatter(new NumberPicker.Formatter() {
            @Override
            public String format(int value) {
                return String.format(Locale.US, "%02d", value);
            }
        });

        LinearLayout pickersRow = new LinearLayout(activity);
        pickersRow.setOrientation(LinearLayout.HORIZONTAL);
        pickersRow.setGravity(Gravity.CENTER_VERTICAL);
        pickersRow.setPadding(AndroidUtilities.dp(16), 0, AndroidUtilities.dp(16), 0);

        int pickerHeightDp = 42 * 5;
        pickersRow.addView(dayPicker, LayoutHelper.createLinear(0, pickerHeightDp, 1.5f));
        pickersRow.addView(hourPicker, LayoutHelper.createLinear(0, pickerHeightDp, 0.75f));
        pickersRow.addView(minutePicker, LayoutHelper.createLinear(0, pickerHeightDp, 0.75f));
        root.addView(pickersRow, LayoutHelper.createLinear(-1, -2));

        // Archive checkbox (only for new subscriptions, or always editable)
        final CheckBoxCell archiveCell;
        if (!isEdit) {
            archiveCell = new CheckBoxCell(activity, 1);
            archiveCell.setText(ru ? "Отправить в архив после подписки" : "Archive after subscribing", "", initialArchive, false);
            archiveCell.setBackground(Theme.getSelectorDrawable(false));
            archiveCell.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    archiveCell.setChecked(!archiveCell.isChecked(), true);
                }
            });
            root.addView(archiveCell, LayoutHelper.createLinear(-1, 48, 0, 4, 0, 0));
        } else {
            archiveCell = null;
        }

        // Action button
        final TextView confirmBtn = new TextView(activity);
        confirmBtn.setGravity(Gravity.CENTER);
        confirmBtn.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 15);
        confirmBtn.setTypeface(AndroidUtilities.bold());
        confirmBtn.setTextColor(accentText);
        confirmBtn.setBackground(Theme.AdaptiveRipple.filledRect(accentColor, 24));
        ScaleStateListAnimator.apply(confirmBtn, 0.02f, 1.2f);
        root.addView(confirmBtn, LayoutHelper.createLinear(-1, 48, 16, 12, 16, 0));

        final Runnable updateChipStyles = new Runnable() {
            @Override
            public void run() {
                for (int i = 0; i < chipViews.length; i++) {
                    boolean sel = (i == selectedChip[0]);
                    chipViews[i].setBackground(Theme.createRoundRectDrawable(
                            AndroidUtilities.dp(16), sel ? accentColor : chipNormalBg
                    ));
                    chipViews[i].setTextColor(sel ? accentText : textBlack);
                }
            }
        };

        final long[] currentTargetSec = new long[]{0L};

        final Runnable syncFromPickers = new Runnable() {
            @Override
            public void run() {
                Calendar c = Calendar.getInstance();
                c.setTimeInMillis(todayMidnightMs + dayPicker.getValue() * 86400000L);
                c.set(Calendar.HOUR_OF_DAY, hourPicker.getValue());
                c.set(Calendar.MINUTE, minutePicker.getValue());
                c.set(Calendar.SECOND, 0);
                c.set(Calendar.MILLISECOND, 0);
                long targetSec = c.getTimeInMillis() / 1000L;
                currentTargetSec[0] = targetSec;

                long nowSec = System.currentTimeMillis() / 1000L;
                boolean valid = targetSec > nowSec + 30L;
                confirmBtn.setEnabled(valid);
                confirmBtn.setAlpha(valid ? 1.0f : 0.45f);

                String dateStr = fullFmt.format(new Date(targetSec * 1000L));
                if (!valid) {
                    confirmBtn.setText(ru ? "Выберите время в будущем" : "Choose a future time");
                } else if (isEdit) {
                    confirmBtn.setText((ru ? "Сохранить · " : "Save · ") + dateStr);
                } else {
                    confirmBtn.setText((ru ? "Подписаться · " : "Subscribe · ") + dateStr);
                }
            }
        };

        final boolean[] programmaticSet = new boolean[]{false};

        final Runnable setPickersToTimestamp = new Runnable() {
            @Override
            public void run() {
                // Uses currentTargetSec[0]
                long targetMs = currentTargetSec[0] * 1000L;
                Calendar c = Calendar.getInstance();
                c.setTimeInMillis(targetMs);
                Calendar midnight = (Calendar) c.clone();
                midnight.set(Calendar.HOUR_OF_DAY, 0);
                midnight.set(Calendar.MINUTE, 0);
                midnight.set(Calendar.SECOND, 0);
                midnight.set(Calendar.MILLISECOND, 0);

                int dayOffset = (int) Math.round((midnight.getTimeInMillis() - todayMidnightMs) / 86400000.0);
                if (dayOffset < 0) dayOffset = 0;
                if (dayOffset > maxDays) dayOffset = maxDays;

                programmaticSet[0] = true;
                dayPicker.setValue(dayOffset);
                hourPicker.setValue(c.get(Calendar.HOUR_OF_DAY));
                minutePicker.setValue(c.get(Calendar.MINUTE));
                programmaticSet[0] = false;
                syncFromPickers.run();
            }
        };

        for (int i = 0; i < presetLabels.length; i++) {
            final int idx = i;
            TextView chip = new TextView(activity);
            chip.setText(presetLabels[i]);
            chip.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 13);
            chip.setTypeface(AndroidUtilities.bold());
            chip.setGravity(Gravity.CENTER);
            chip.setPadding(AndroidUtilities.dp(14), AndroidUtilities.dp(7), AndroidUtilities.dp(14), AndroidUtilities.dp(7));
            ScaleStateListAnimator.apply(chip, 0.04f, 1.2f);
            chip.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    selectedChip[0] = idx;
                    updateChipStyles.run();
                    currentTargetSec[0] = (System.currentTimeMillis() / 1000L) + presetSeconds[idx];
                    setPickersToTimestamp.run();
                }
            });
            chipViews[i] = chip;
            chipsRow.addView(chip, LayoutHelper.createLinear(-2, -2, 0, 0, 6, 0));
        }

        NumberPicker.OnValueChangeListener pickerListener = new NumberPicker.OnValueChangeListener() {
            @Override
            public void onValueChange(NumberPicker picker, int oldVal, int newVal) {
                if (!programmaticSet[0]) {
                    selectedChip[0] = -1;
                    updateChipStyles.run();
                }
                syncFromPickers.run();
            }
        };
        dayPicker.setOnValueChangedListener(pickerListener);
        hourPicker.setOnValueChangedListener(pickerListener);
        minutePicker.setOnValueChangedListener(pickerListener);

        long nowSec = System.currentTimeMillis() / 1000L;
        if (initialLeaveAt > nowSec + 60L) {
            currentTargetSec[0] = initialLeaveAt;
            selectedChip[0] = -1;
        } else {
            selectedChip[0] = 5; // 7d default
            currentTargetSec[0] = nowSec + presetSeconds[5];
        }
        updateChipStyles.run();
        setPickersToTimestamp.run();

        sheet.setCustomView(root);

        confirmBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                long target = currentTargetSec[0];
                if (target <= (System.currentTimeMillis() / 1000L) + 30L) {
                    return;
                }
                lastResultLeaveAt = target;
                lastResultArchive = archiveCell != null ? archiveCell.isChecked() : initialArchive;
                lastResultConfirmed = true;
                sheet.dismiss();
                if (onConfirm != null) {
                    onConfirm.run();
                }
            }
        });

        sheet.show();
    }
}
