---
env_type: vm
tags: [disk, storage]
---

# VM 数据盘未识别

## 现象

银河平台 VM 部署后数据磁盘不可见，lsblk 只显示系统盘。

## 根因

数据盘未挂载或内核未识别块设备。

## 修复

检查 /proc/partitions，重新扫描 SCSI 总线，挂载数据盘。