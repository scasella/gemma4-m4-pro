use crate::compute::ffi::{apply_chat_template, ChatTemplateMessage};

#[derive(Debug, Clone, Copy)]
pub struct PromptMessage<'a> {
    pub role: &'a str,
    pub content: &'a str,
}

pub fn format_chat_prompt(
    messages: &[PromptMessage<'_>],
    chat_template: Option<&str>,
) -> anyhow::Result<String> {
    if let Some(template) = chat_template {
        if is_gemma4_template(template) {
            return Ok(format_gemma4_text_prompt(messages));
        }

        let template_messages: Vec<_> = messages
            .iter()
            .map(|message| ChatTemplateMessage {
                role: message.role,
                content: message.content,
            })
            .collect();

        if let Some(prompt) = apply_chat_template(Some(template), &template_messages)? {
            return Ok(prompt);
        }
    }

    Ok(format_chatml_prompt(messages))
}

fn is_gemma4_template(template: &str) -> bool {
    template.contains("<|turn>") && template.contains("<turn|>")
}

fn format_chatml_prompt(messages: &[PromptMessage<'_>]) -> String {
    let mut prompt = String::new();
    for message in messages {
        prompt.push_str("<|im_start|>");
        prompt.push_str(message.role);
        prompt.push('\n');
        prompt.push_str(message.content);
        prompt.push_str("<|im_end|>\n");
    }
    prompt.push_str("<|im_start|>assistant\n");
    prompt
}

fn format_gemma4_text_prompt(messages: &[PromptMessage<'_>]) -> String {
    let mut prompt = String::from("<bos>");
    let mut index = 0usize;

    // Gemma 4's template only exposes one top-level system turn. Collapse
    // consecutive leading system/developer messages into that slot.
    let mut system_chunks = Vec::new();
    while let Some(message) = messages.get(index) {
        if !matches!(message.role, "system" | "developer") {
            break;
        }

        let trimmed = message.content.trim();
        if !trimmed.is_empty() {
            system_chunks.push(trimmed);
        }
        index += 1;
    }

    if !system_chunks.is_empty() {
        prompt.push_str("<|turn>system\n");
        prompt.push_str(&system_chunks.join("\n\n"));
        prompt.push_str("<turn|>\n");
    }

    for message in &messages[index..] {
        let role = match message.role {
            "assistant" => "model",
            "developer" => "system",
            other => other,
        };
        let content = if role == "model" {
            strip_gemma4_thinking(message.content)
        } else {
            message.content.trim().to_string()
        };

        prompt.push_str("<|turn>");
        prompt.push_str(role);
        prompt.push('\n');
        prompt.push_str(&content);
        prompt.push_str("<turn|>\n");
    }

    prompt.push_str("<|turn>model\n<|channel>thought\n<channel|>\n");
    prompt
}

fn strip_gemma4_thinking(text: &str) -> String {
    let mut result = String::new();
    for part in text.split("<channel|>") {
        if let Some((visible, _)) = part.split_once("<|channel>") {
            result.push_str(visible);
        } else {
            result.push_str(part);
        }
    }
    result.trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_gemma4_user_prompt() {
        let messages = [PromptMessage {
            role: "user",
            content: "hello",
        }];

        let prompt = format_chat_prompt(
            &messages,
            Some("{{ bos_token }}<|turn>user\n{{ message['content'] }}<turn|>\n<|turn>model\n"),
        )
        .unwrap();

        assert_eq!(
            prompt,
            "<bos><|turn>user\nhello<turn|>\n<|turn>model\n<|channel>thought\n<channel|>\n"
        );
    }

    #[test]
    fn formats_gemma4_system_and_strips_assistant_thinking() {
        let messages = [
            PromptMessage {
                role: "system",
                content: "Be terse.",
            },
            PromptMessage {
                role: "assistant",
                content: "visible<|channel>thought\nhidden<channel|>answer",
            },
            PromptMessage {
                role: "user",
                content: "next",
            },
        ];

        let prompt = format_chat_prompt(
            &messages,
            Some("{{ bos_token }}<|turn>system\n...<turn|>\n<|turn>model\n"),
        )
        .unwrap();

        assert_eq!(
            prompt,
            "<bos><|turn>system\nBe terse.<turn|>\n<|turn>model\nvisibleanswer<turn|>\n<|turn>user\nnext<turn|>\n<|turn>model\n<|channel>thought\n<channel|>\n"
        );
    }

    #[test]
    fn falls_back_to_chatml_without_template() {
        let messages = [PromptMessage {
            role: "user",
            content: "hello",
        }];

        let prompt = format_chat_prompt(&messages, None).unwrap();

        assert_eq!(
            prompt,
            "<|im_start|>user\nhello<|im_end|>\n<|im_start|>assistant\n"
        );
    }
}
