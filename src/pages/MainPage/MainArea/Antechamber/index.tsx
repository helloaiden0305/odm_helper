/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { useDispatch } from 'react-redux';
import { isMobile } from '@/utils/utils';
import InvokeButton from '@/pages/MainPage/MainArea/Antechamber/InvokeButton';
import { useRTC, useScene } from '@/lib/useCommon';
import AIChangeCard from '@/components/AiChangeCard';
import { localJoinRoom, updateFullScreen, updateShowSubtitle } from '@/store/slices/room';
import style from './index.module.less';

function Antechamber() {
  const dispatch = useDispatch();
  const rtcConfig = useRTC();
  const { isScreenMode, isAvatarScene } = useScene();

  const handleJoinRoom = () => {
    if (!rtcConfig?.RoomId || !rtcConfig?.UserId) {
      return;
    }

    dispatch(updateFullScreen({ isFullScreen: !isMobile() && !isScreenMode && !isAvatarScene })); // 初始化
    dispatch(updateShowSubtitle({ isShowSubtitle: !isAvatarScene }));
    dispatch(
      localJoinRoom({
        roomId: rtcConfig.RoomId,
        user: {
          username: rtcConfig.UserId,
          userId: rtcConfig.UserId,
          publishAudio: false,
          publishVideo: false,
          publishScreen: false,
        },
      })
    );
  };

  return (
    <div className={`${style.wrapper} ${isMobile() ? style.mobile : ''}`}>
      <AIChangeCard />
      <InvokeButton
        onClick={handleJoinRoom}
        loading={!rtcConfig?.RoomId}
        className={style['invoke-btn']}
      />
      {isMobile() ? null : (
        <div className={style.description}>RTC + ASR + RAG + LLM + TTS 工程问题处理链路</div>
      )}
    </div>
  );
}

export default Antechamber;
