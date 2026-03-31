# Automatic Timeout Feature

## No More Manual Configuration! 🎉

You no longer need to run export commands or manually set timeout values. The server **automatically** calculates the perfect timeout based on your video's length.

## How It Works

When you download a video:

1. The server inspects your video and gets its duration
2. It calculates: `timeout = (duration × 1.5) + 120 seconds`
3. The timeout is automatically set for that download
4. Your video downloads without any timeout errors

## Examples

| Video Length | Calculated Timeout | Result |
|---|---|---|
| 5 minutes (300s) | 570s | ✅ Downloads easily |
| 30 minutes (1800s) | 2820s | ✅ No problems |
| 1 hour (3600s) | 5520s | ✅ Automatic support |
| 2 hours (7200s) | 10920s | ✅ Works perfectly |
| 3 hours (10800s) | 16320s | ✅ Long videos supported |
| 6 hours (21600s) | 32520s | ✅ Even long videos work |

## Just Run It

**Linux/Mac:**
```bash
./run.sh
```

**Windows:**
```cmd
run.bat
```

That's it! No export commands needed. Videos of any length will work.

## Advanced: Custom Base Timeout

If you have unreliable internet and want extra buffer, you can set a custom base timeout:

```bash
export VIDSLICER_DOWNLOAD_TIMEOUT=600  # 10 minute base buffer
./run.sh
```

The final timeout will still be calculated as: `(duration × 1.5) + base_timeout`

## What If I Have Very Slow Internet?

The automatic calculation includes a 1.5× multiplier to account for slow networks:
- A 1-hour video gets timeout of: (3600 × 1.5) + 120 = 5520 seconds
- This is 92 minutes of allowed download time for a 60-minute video
- Plenty of buffer for slow connections!

## Troubleshooting

**Still getting timeout errors?**

1. Check your internet speed during download
2. Try downloading during off-peak hours
3. Set a custom base timeout (see Advanced section above)

**Very unstable connection?**

```bash
export VIDSLICER_DOWNLOAD_TIMEOUT=1200  # 20 minute base buffer
./run.sh
```

## Summary

✅ No manual export commands  
✅ No configuration file editing  
✅ Automatic support for 1-hour, 2-hour, 6-hour videos  
✅ Adaptive timeout based on actual video length  
✅ 1.5× multiplier provides buffer for slow networks  

Just run `./run.sh` and download any video!
