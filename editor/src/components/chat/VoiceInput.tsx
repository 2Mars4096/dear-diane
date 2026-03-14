import { useState, useRef, useCallback, useEffect } from "react";
import { Mic } from "lucide-react";

interface SpeechRecognitionEvent {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}

interface Props {
  onTranscript: (text: string) => void;
  size?: number;
}

export default function VoiceInput({ onTranscript, size = 16 }: Props) {
  const [isListening, setIsListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [unsupported, setUnsupported] = useState(false);
  const recognitionRef = useRef<any>(null);

  const startListening = useCallback(() => {
    const SpeechRecognition =
      (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      setUnsupported(true);
      setTimeout(() => setUnsupported(false), 3000);
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let finalText = "";
      let interimText = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          finalText += result[0].transcript;
        } else {
          interimText += result[0].transcript;
        }
      }
      if (finalText) onTranscript(finalText);
      setInterim(interimText);
    };

    recognition.onerror = () => setIsListening(false);
    recognition.onend = () => {
      setIsListening(false);
      setInterim("");
    };

    recognition.start();
    recognitionRef.current = recognition;
    setIsListening(true);
  }, [onTranscript]);

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setIsListening(false);
    setInterim("");
  }, []);

  useEffect(() => {
    return () => {
      recognitionRef.current?.stop();
    };
  }, []);

  return (
    <div className="relative">
      <button
        onClick={isListening ? stopListening : startListening}
        className={`p-1 rounded transition-colors ${
          isListening
            ? "bg-red-600 text-white animate-pulse"
            : "text-gray-400 hover:text-indigo-500"
        }`}
        title={
          unsupported
            ? "Voice input not supported in this browser"
            : isListening
              ? "Stop recording"
              : "Voice input"
        }
      >
        <Mic size={size} />
      </button>
      {isListening && interim && (
        <div className="absolute bottom-full mb-1 left-1/2 -translate-x-1/2 px-2 py-1 bg-gray-800 rounded text-[11px] text-gray-300 whitespace-nowrap max-w-[200px] overflow-hidden text-ellipsis shadow-lg border border-gray-700 z-10">
          {interim}
        </div>
      )}
      {unsupported && (
        <div className="absolute bottom-full mb-1 left-1/2 -translate-x-1/2 px-2 py-1 bg-red-900/80 rounded text-[10px] text-red-300 whitespace-nowrap shadow-lg border border-red-700 z-10">
          Voice input not supported
        </div>
      )}
    </div>
  );
}
